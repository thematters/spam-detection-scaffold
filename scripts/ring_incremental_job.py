#!/usr/bin/env python3
"""軸一 D 增量版 ring 偵測 job（VPC runner JOB=ring-incremental / moment-ring-incremental）。

與全量版（ring_detect_job.py，30 天窗整掃）的關係——**兩者並存、分工**：
  - 增量版（本檔）：高頻跑（小時級）。S3 維護 30 天滾動「貼文指紋狀態」，每次只向
    replica 抓「水位線之後的新貼文」、app 端算 normalized_fingerprint（含繁簡/emoji
    正規化，口徑比 SQL 粗篩更準）、append 回狀態，然後**只對被新貼文碰到的指紋群**
    重新分群與處置。掃描量 ∝ 新貼文數，與 30 天窗大小無關。
  - 全量版：每日跑一次當「對帳」。狀態檔冷啟動期（部署後前 30 天內狀態未滿窗）
    的漏網、貼文編輯/刪除造成的漂移，都由它兜底。

冷啟動：狀態為空時只回看 INCREMENTAL_BOOTSTRAP_HOURS（預設 24h），不做 30 天重建
——歷史 ring 本來就由全量版守著；增量版的職責是把「新攻擊→進控制台/凍結」的
延遲從天級壓到小時級。

處置端（upsert / AUTO_FREEZE 閘門 / 老帳號豁免 / dismissed 不推翻）與全量版共用
同一套函式（ring_detect_job.py），行為完全一致。

環境變數（在全量版之上新增）：
  BUCKET                       狀態桶（沿用 runner 既有 BUCKET；建議 matters-spam-training-samples）
  STATE_PREFIX                 狀態前綴（預設 ring-state）
  INCREMENTAL_OVERLAP_MINUTES  水位線重疊回看，防 clock skew / 邊界漏抓（預設 10）
  INCREMENTAL_BOOTSTRAP_HOURS  冷啟動回看窗（預設 24）
其餘（PG_DSN / MATTERS_OSS_* / CONTENT_TYPE / MIN_AUTHORS / SINGLE_AUTHOR_MIN_POSTS /
NEW_ACCOUNT_DAYS / MAX_ARTICLES_PER_RING / DRY_RUN / AUTO_FREEZE*）同 ring_detect_job.py。
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "eval"))
import ring_signals  # noqa: E402
from ring_detect_job import (  # noqa: E402
    CONTENT_TYPES,
    auto_freeze,
    auto_freeze_eligible,
    build_candidate,
    filter_empty_members,
    strip_internal_keys,
    _merge_by_fingerprint,
    _post_upsert,
)

STATE_WINDOW_DAYS = 30  # 與全量版 DAYS 同窗；狀態列超過此齡即淘汰

# 增量抓取：只撈水位線之後的新貼文（created_at 有 index，量 ∝ 發文速率）。
# 不在這裡過濾 user.state——成員狀態在處置前以最新值批次覆核（見 fetch_member_states）。
NEW_POSTS_QUERIES = {
    "article": """
SELECT a.id, a.author_id, u.user_name AS author_name,
       u.created_at AS user_created_at, a.created_at,
       coalesce(av.title,'') || ' ' || coalesce(ac.content,'') AS content
FROM article a
JOIN article_version_newest av ON av.article_id = a.id
JOIN article_content ac        ON ac.id = av.content_id
JOIN "user" u                  ON u.id = a.author_id
WHERE a.created_at > %(since)s AND a.state = 'active'
ORDER BY a.created_at
""",
    "moment": """
SELECT m.id, m.author_id, u.user_name AS author_name,
       u.created_at AS user_created_at, m.created_at,
       coalesce(m.content, '') AS content
FROM moment m
JOIN "user" u ON u.id = m.author_id
WHERE m.created_at > %(since)s AND m.state = 'active'
ORDER BY m.created_at
""",
}

MEMBER_STATE_QUERY = """
SELECT id, state FROM "user" WHERE id = ANY(%(ids)s)
"""


# --- 純函式（可測）---

def to_state_row(post: dict) -> dict | None:
    """DB 列 → 狀態列。正規化後無文字（純圖/emoji/url）的貼文不進狀態——
    它們永遠不該構成 ring（F3a 的每貼文版，比全量版的成員級過濾更前置）。"""
    fp = ring_signals.normalized_fingerprint(post.get("content") or "")
    if fp == ring_signals.EMPTY_FINGERPRINT:
        return None
    return {
        "id": str(post["id"]),
        "author_id": str(post["author_id"]),
        "author_name": post.get("author_name") or str(post["author_id"]),
        "user_created_at": _iso(post.get("user_created_at")),
        "created_at": _iso(post.get("created_at")),
        "fp": fp,
    }


def _iso(v) -> str | None:
    """datetime → ISO 字串；replica 的 timestamp 欄位是 naive UTC，一律補上 tz
    再序列化，讓後續比較全在 aware 世界（naive/aware 混比會 TypeError）。"""
    if v is None:
        return None
    if hasattr(v, "isoformat"):
        if getattr(v, "tzinfo", None) is None:
            v = v.replace(tzinfo=dt.timezone.utc)
        return v.isoformat()
    return str(v)


def _parse_ts(s: str) -> dt.datetime:
    t = dt.datetime.fromisoformat(s)
    return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)


def watermark_of(rows: list, *, bootstrap_hours: int, now: dt.datetime) -> dt.datetime:
    """水位線＝狀態中最新貼文時間；狀態為空＝冷啟動，只回看 bootstrap 窗。"""
    times = [r["created_at"] for r in rows if r.get("created_at")]
    if not times:
        return now - dt.timedelta(hours=bootstrap_hours)
    return _parse_ts(max(times))


def plan_candidates(all_rows: list, new_rows: list, *, min_authors: int,
                    single_author_min_posts: int) -> list:
    """只對「被新貼文碰到的指紋」重新分群（未被碰到的群不可能改變）。
    回傳 [{fp, member_ids, post_ids, n_posts, n_authors, rows}]，門檻同全量版：
    跨帳號 ≥ min_authors，或單帳號 ≥ single_author_min_posts 篇（F1a）。"""
    touched = {r["fp"] for r in new_rows}
    by_fp: dict = {}
    seen_posts: set = set()
    for r in all_rows:
        if r["fp"] not in touched or r["id"] in seen_posts:
            continue
        seen_posts.add(r["id"])
        by_fp.setdefault(r["fp"], []).append(r)
    out = []
    for fp, rows in by_fp.items():
        authors = {r["author_id"] for r in rows}
        if not (len(authors) >= min_authors
                or (len(authors) == 1 and len(rows) >= single_author_min_posts)):
            continue
        out.append({
            "fp": fp,
            "member_ids": sorted(authors),
            "post_ids": sorted({r["id"] for r in rows}),
            "n_posts": len(rows),
            "n_authors": len(authors),
            "rows": rows,
        })
    return sorted(out, key=lambda g: -g["n_authors"])


def drop_processed_members(group: dict, states: dict) -> dict | None:
    """處置前以最新 user.state 覆核：已 frozen/banned/archived 的成員剔除
    （與全量版 SQL 的 JOIN 條件同口徑——否則凍過的群每輪重新冒出）。"""
    bad = {uid for uid, st in states.items() if st in ("banned", "frozen", "archived")}
    if not bad:
        return group
    rows = [r for r in group["rows"] if r["author_id"] not in bad]
    if not rows:
        return None
    authors = {r["author_id"] for r in rows}
    return {**group, "rows": rows, "member_ids": sorted(authors),
            "post_ids": sorted({r["id"] for r in rows}),
            "n_posts": len(rows), "n_authors": len(authors)}


def group_to_sql_row(group: dict, count_col: str, *, new_account_days: int,
                     now: dt.datetime) -> dict:
    """把增量群組整形成全量版 build_candidate 吃的 row（new_account_ratio 以
    每帳號一票、user_created_at 對 now 齡計算，與 SQL 版同口徑）。"""
    cutoff = now - dt.timedelta(days=new_account_days)
    per_author: dict = {}
    for r in group["rows"]:
        uca = r.get("user_created_at")
        if uca is None:
            continue
        per_author[r["author_id"]] = _parse_ts(uca) >= cutoff
    ratio = (sum(per_author.values()) / len(per_author)) if per_author else None
    return {
        "template_fam": group["fp"],
        count_col: group["n_posts"],
        "n_authors": group["n_authors"],
        "new_account_ratio": ratio,
        "author_ids": group["member_ids"],
    }


# --- S3 狀態 I/O ---

def _s3():
    import boto3

    return boto3.client("s3")


def load_state(bucket: str, prefix: str, content_type: str, *, now: dt.datetime) -> list:
    """讀 30 天窗內的狀態列（一 run 一物件，鍵含 run 時戳；過窗物件順手刪除）。"""
    s3 = _s3()
    cutoff = now - dt.timedelta(days=STATE_WINDOW_DAYS)
    rows: list = []
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=f"{prefix}/{content_type}/"):
        for obj in page.get("Contents", []):
            body = s3.get_object(Bucket=bucket, Key=obj["Key"])["Body"].read()
            kept = []
            for line in body.decode().splitlines():
                if not line.strip():
                    continue
                r = json.loads(line)
                if r.get("created_at") and _parse_ts(r["created_at"]) >= cutoff:
                    kept.append(r)
            if kept:
                rows.extend(kept)
            elif obj["Key"].endswith(".jsonl"):
                s3.delete_object(Bucket=bucket, Key=obj["Key"])  # 整檔過窗 → 淘汰
    return rows


def append_state(bucket: str, prefix: str, content_type: str, rows: list,
                 *, now: dt.datetime) -> str:
    key = f"{prefix}/{content_type}/{now.strftime('%Y%m%dT%H%M%S')}.jsonl"
    body = "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n"
    _s3().put_object(Bucket=bucket, Key=key, Body=body.encode())
    return key


# --- 主流程 ---

def main() -> int:
    content_type = os.environ.get("CONTENT_TYPE", "article")
    if content_type not in NEW_POSTS_QUERIES:
        print(f"CONTENT_TYPE must be one of {sorted(NEW_POSTS_QUERIES)}", file=sys.stderr)
        return 2
    spec = CONTENT_TYPES[content_type]
    min_authors = int(os.environ.get("MIN_AUTHORS", "3"))
    single_min = int(os.environ.get("SINGLE_AUTHOR_MIN_POSTS", "3"))
    new_account_days = int(os.environ.get("NEW_ACCOUNT_DAYS", "30"))
    max_articles = int(os.environ.get("MAX_ARTICLES_PER_RING", "200"))
    overlap_min = int(os.environ.get("INCREMENTAL_OVERLAP_MINUTES", "10"))
    bootstrap_h = int(os.environ.get("INCREMENTAL_BOOTSTRAP_HOURS", "24"))
    bucket = os.environ.get("BUCKET")
    state_prefix = os.environ.get("STATE_PREFIX", "ring-state")
    dry_run = bool(os.environ.get("DRY_RUN"))
    auto_freeze_on = bool(os.environ.get("AUTO_FREEZE"))
    freeze_cfg = {
        "high_authors": int(os.environ.get("AUTO_FREEZE_HIGH_AUTHORS", "3")),
        "new_ratio_hi": float(os.environ.get("AUTO_FREEZE_NEW_RATIO_HI", "0.8")),
        "bot_ratio_hi": float(os.environ.get("AUTO_FREEZE_BOT_RATIO_HI", "0.5")),
        "old_exempt_ratio": float(os.environ.get("AUTO_FREEZE_OLD_EXEMPT", "0.34")),
    }
    dsn = os.environ.get("PG_DSN")
    if not dsn or not bucket:
        print("PG_DSN and BUCKET required", file=sys.stderr)
        return 2

    import psycopg
    from psycopg.rows import dict_row

    now = dt.datetime.now(dt.timezone.utc)
    state = load_state(bucket, state_prefix, content_type, now=now)
    since = watermark_of(state, bootstrap_hours=bootstrap_h, now=now) - dt.timedelta(
        minutes=overlap_min)
    known_ids = {r["id"] for r in state}
    print(f"state: {len(state)} rows in {STATE_WINDOW_DAYS}d window; fetch since {since.isoformat()}")

    with psycopg.connect(dsn) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            # 欄位是 naive timestamp（UTC）；傳 naive 參數避免 session timezone 亂入
            cur.execute(NEW_POSTS_QUERIES[content_type],
                        {"since": since.astimezone(dt.timezone.utc).replace(tzinfo=None)})
            fetched = cur.fetchall()
        new_rows = [
            r for r in (to_state_row(p) for p in fetched)
            if r is not None and r["id"] not in known_ids
        ]
        print(f"fetched {len(fetched)} new post(s), {len(new_rows)} with text fingerprint")
        if not new_rows:
            return 0

        groups = plan_candidates(state + new_rows, new_rows,
                                 min_authors=min_authors,
                                 single_author_min_posts=single_min)
        # 成員狀態覆核（批次一次撈齊）＋ 內容精修，整形回全量版 pipeline
        candidates = []
        all_member_ids = sorted({m for g in groups for m in g["member_ids"]})
        states: dict = {}
        if all_member_ids:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(MEMBER_STATE_QUERY, {"ids": all_member_ids})
                states = {str(r["id"]): r["state"] for r in cur.fetchall()}
        for g in groups:
            g = drop_processed_members(g, states)
            if g is None:
                continue
            if not (g["n_authors"] >= min_authors
                    or (g["n_authors"] == 1 and g["n_posts"] >= single_min)):
                continue
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(spec["content_query"],
                            {"ids": g["post_ids"][:max_articles],
                             "new_account_days": str(new_account_days)})
                posts = cur.fetchall()
            items = [
                {"content": p["content"] or "",
                 "author": p["author_name"] or str(p["author_id"]),
                 "author_id": p["author_id"],
                 "is_new_account": p.get("is_new_account")}
                for p in posts
            ]
            if not items:
                continue
            row = group_to_sql_row(g, spec["count_col"],
                                   new_account_days=new_account_days, now=now)
            row, items = filter_empty_members(row, items, spec["count_col"])
            if int(row.get("n_authors") or 0) <= 0 or not items:
                continue
            cand = build_candidate(row, items, count_col=spec["count_col"])
            # 審查 F1/F3：凍結只准碰「本輪從 DB 抓回內容驗證過」的成員——
            # state 檔宣稱的成員（可能被汙染或過期）不進凍結名單；截斷降級人工。
            cand["_verifiedMemberIds"] = sorted({str(it["author_id"]) for it in items})
            if len(g["post_ids"]) > max_articles:
                cand["_truncated"] = True
            candidates.append(cand)
    candidates = _merge_by_fingerprint(candidates)
    print(f"{len(candidates)} candidate(s) touched by new posts")

    if dry_run:
        for c in candidates:
            c["autoFreezeEligibleDryRun"] = auto_freeze_eligible(c, **freeze_cfg)
        print(json.dumps(candidates, ensure_ascii=False, indent=2)[:4000])
        return 0

    def commit_state() -> None:
        # 水位線在「處置寫回成功後」才推進；upsert 失敗＝不落狀態，下輪重抓重送
        # （upsert 以 fingerprint 冪等，重送安全）。
        key = append_state(bucket, state_prefix, content_type, new_rows, now=now)
        print(f"state appended: s3://{bucket}/{key} (+{len(new_rows)})")

    if not candidates:
        commit_state()
        return 0
    endpoint = os.environ.get("MATTERS_OSS_GQL_ENDPOINT")
    token = os.environ.get("MATTERS_OSS_ADMIN_TOKEN")
    if not endpoint or not token:
        print("MATTERS_OSS_GQL_ENDPOINT / MATTERS_OSS_ADMIN_TOKEN not set — shadow only",
              file=sys.stderr)
        print(json.dumps(candidates, ensure_ascii=False)[:2000])
        commit_state()
        return 0
    result = _post_upsert(endpoint, token, strip_internal_keys(candidates),
                          with_rings=auto_freeze_on)
    print(f"upserted: {json.dumps({k: v for k, v in result.items() if k != 'rings'})}")
    commit_state()
    if auto_freeze_on:
        summary = auto_freeze(endpoint, token, candidates, result.get("rings") or [], freeze_cfg)
        print(f"auto-freeze: {json.dumps(summary, ensure_ascii=False)}")
        if summary["errors"]:
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
