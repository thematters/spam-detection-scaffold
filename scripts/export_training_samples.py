#!/usr/bin/env python3
"""Layer 1 被動增量抽取 runner（軸二 L1）。

從 matters-server read-replica 抽「已處置/已確認」的留言，去識別化後 append 進
S3 訓練桶。趁內容還在 live DB（archiveUser 不刪留言、但 clearCommunityWatchOriginal-
Content 會清快照），定期落袋成標註語料。SQL 邏輯與 sql/extract_spam_training_incremental.sql
等價（該檔是 psql 版可讀參考；本檔是 CI runner，用 psycopg 參數化）。

去識別化：author_id / comment_id → HMAC-SHA256（salt 走 secret），穩定去重、不可還原。
只留 content + label + metadata，不留任何可聯繫個資。

watermark：以 S3 上的 _watermark.json（max updated_at）做增量起點，避免漏抓/重抓。

環境變數：
  PG_READONLY_CONN   read-replica 連線字串（postgresql://...）
  EXPORT_S3_BUCKET   目的桶（如 matters-spam-training-samples）
  EXPORT_S3_PREFIX   前綴（預設 comment-training-samples）
  HASH_SALT          HMAC salt（secret）
  SPAM_THRESHOLD     模型分數視為 spam 門檻（預設 0.80）
  LOOKBACK_DAYS      無 watermark 時的回溯天數（預設 30）
"""
from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import io
import json
import os

import boto3
import pandas as pd
import psycopg

QUERY = """
WITH cw_removed AS (
  SELECT cwa.comment_id,
         cwa.comment_author_id AS author_id,
         COALESCE(cwa.original_content, c.content) AS content,
         cwa.reason AS cw_reason,
         cwa.updated_at
  FROM community_watch_action cwa
  LEFT JOIN comment c ON c.id = cwa.comment_id
  WHERE cwa.action_state = 'active'
    AND cwa.review_state <> 'reversed'
    AND cwa.updated_at >= %(since)s
    AND COALESCE(cwa.original_content, c.content) IS NOT NULL
),
restricted_or_flagged AS (
  SELECT c.id AS comment_id, c.author_id, c.content,
         NULL::text AS cw_reason, c.updated_at,
         EXISTS (SELECT 1 FROM user_restriction ur WHERE ur.user_id = c.author_id) AS is_restricted,
         c.is_spam, c.spam_score
  FROM comment c
  WHERE c.updated_at >= %(since)s
    AND c.content IS NOT NULL
    AND ( EXISTS (SELECT 1 FROM user_restriction ur WHERE ur.user_id = c.author_id)
       OR c.is_spam = true
       OR (c.spam_score IS NOT NULL AND c.spam_score >= %(threshold)s) )
)
SELECT comment_id, author_id, content, 1 AS label,
       ARRAY_REMOVE(ARRAY[
         CASE WHEN cw_reason IS NOT NULL THEN 'community_watch:' || cw_reason END,
         CASE WHEN is_restricted THEN 'user_restriction' END,
         CASE WHEN is_spam THEN 'admin_is_spam' END,
         CASE WHEN spam_score >= %(threshold)s THEN 'model_score' END
       ], NULL) AS label_source,
       spam_score, updated_at
FROM (
  SELECT comment_id, author_id, content, cw_reason,
         NULL::boolean AS is_restricted, NULL::boolean AS is_spam,
         NULL::numeric AS spam_score, updated_at FROM cw_removed
  UNION ALL
  SELECT comment_id, author_id, content, cw_reason,
         is_restricted, is_spam, spam_score, updated_at FROM restricted_or_flagged
) pos
UNION ALL
SELECT cwa.comment_id, cwa.comment_author_id AS author_id,
       COALESCE(cwa.original_content, c.content) AS content, 0 AS label,
       ARRAY['reversed_moderation'] AS label_source, c.spam_score, cwa.updated_at
FROM community_watch_action cwa
LEFT JOIN comment c ON c.id = cwa.comment_id
WHERE (cwa.review_state = 'reversed' OR cwa.action_state IN ('restored', 'voided'))
  AND cwa.updated_at >= %(since)s
  AND COALESCE(cwa.original_content, c.content) IS NOT NULL;
"""


def _hash(salt: str, value: str) -> str:
    return hmac.new(salt.encode(), str(value).encode(), hashlib.sha256).hexdigest()


def _read_watermark(s3, bucket: str, prefix: str, lookback_days: int) -> str:
    key = f"{prefix}/_watermark.json"
    try:
        obj = s3.get_object(Bucket=bucket, Key=key)
        return json.loads(obj["Body"].read())["max_updated_at"]
    except s3.exceptions.NoSuchKey:
        since = dt.datetime.utcnow() - dt.timedelta(days=lookback_days)
        return since.isoformat()


def _write_watermark(s3, bucket: str, prefix: str, value: str) -> None:
    s3.put_object(
        Bucket=bucket,
        Key=f"{prefix}/_watermark.json",
        Body=json.dumps({"max_updated_at": value}).encode(),
    )


def main() -> int:
    conn_str = os.environ["PG_READONLY_CONN"]
    bucket = os.environ["EXPORT_S3_BUCKET"]
    prefix = os.environ.get("EXPORT_S3_PREFIX", "comment-training-samples")
    salt = os.environ["HASH_SALT"]
    threshold = float(os.environ.get("SPAM_THRESHOLD", "0.80"))
    lookback = int(os.environ.get("LOOKBACK_DAYS", "30"))

    s3 = boto3.client("s3")
    since = _read_watermark(s3, bucket, prefix, lookback)
    print(f"since={since} threshold={threshold}")

    with psycopg.connect(conn_str) as conn:
        df = pd.read_sql(
            QUERY, conn, params={"since": since, "threshold": threshold}
        )

    if df.empty:
        print("no new samples; nothing to export")
        return 0

    new_max = str(df["updated_at"].max())
    # de-identify: replace raw ids with salted HMAC; drop nothing else PII-bearing
    df["author_hash"] = df["author_id"].map(lambda v: _hash(salt, v))
    df["comment_hash"] = df["comment_id"].map(lambda v: _hash(salt, v))
    df = df.drop(columns=["author_id", "comment_id"])

    buf = io.BytesIO()
    df.to_parquet(buf, index=False, compression="gzip")
    buf.seek(0)
    stamp = dt.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    day = dt.datetime.utcnow().strftime("%Y/%m/%d")
    key = f"{prefix}/dt={day}/samples-{stamp}.parquet.gzip"
    s3.put_object(Bucket=bucket, Key=key, Body=buf.getvalue())
    print(f"wrote {len(df)} samples (spam={int((df.label==1).sum())} "
          f"ham={int((df.label==0).sum())}) -> s3://{bucket}/{key}")

    _write_watermark(s3, bucket, prefix, new_max)
    print(f"watermark -> {new_max}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
