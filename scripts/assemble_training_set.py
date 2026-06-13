#!/usr/bin/env python3
"""軸二 L3：把 L1 被動抽取 + L2 主動擷取合併成乾淨的留言訓練集。

輸入（皆已去識別化，欄位含 comment_hash）：
  L1  scaffold/scripts/export_training_samples.py 寫的 parquet
      （欄位：content, label, label_source[list], spam_score, updated_at,
        author_hash, comment_hash）
  L2  workers/spam_sample_worker.py 寫的 JSONL
      （欄位：label, text, labelSource, score, commentHash, authorHash, occurredAt）

去重與標籤裁決（核心 = resolve()）：
  - 以 comment_hash 分組；一則留言只留一筆。
  - **ham 覆蓋 spam**：若該留言任一筆是 label=0（reversed/restored 的處置 = 誤殺），
    最終判 ham —— 人工推翻優先於舊的 spam 標籤。
  - 同 label 內取「來源強度最高、時間最新」者。
  - 來源強度：人工守望/推翻 > 受限作者/管理員 > 純模型分數。
  - 加 label_weight：人工確認的樣本權重高（壓低誤殺，對應受保護 1% 的目標）。

輸出乾淨 parquet（S3 或本機）。文章模型訓練集（article parquet）屬不同 domain，
不在此合併。
"""
from __future__ import annotations

import argparse

import pandas as pd

# 來源強度（數字越大越可信）
SOURCE_RANK = {
    "community_watch": 4,      # 人工守望移除/清除
    "reversed_moderation": 4,  # 人工推翻（ham, 來自 L1）
    "community_watch_clear": 4,
    "community_watch_remove": 4,
    "user_restriction": 3,
    "admin_is_spam": 3,
    "model_score": 1,
}
# 最終來源強度 → 訓練權重（人工確認壓低誤殺）
RANK_WEIGHT = {4: 2.0, 3: 1.0, 1: 0.5}


def _rank_of(label_source) -> int:
    """label_source 可能是 list（L1）或 str（L2, 形如 'community_watch_remove:porn_ad'）。"""
    if isinstance(label_source, str):
        sources = [label_source]
    elif isinstance(label_source, (list, tuple)):
        sources = list(label_source)
    else:
        sources = []
    best = 0
    for s in sources:
        key = str(s).split(":")[0]
        best = max(best, SOURCE_RANK.get(key, 1))
    return best or 1


def resolve(df: pd.DataFrame) -> pd.DataFrame:
    """去重 + 標籤裁決。輸入需含欄位：comment_hash, label, label_source, text,
    score, occurred_at。回傳每個 comment_hash 一筆，含 label_weight。"""
    if df.empty:
        return df.assign(label_weight=[])
    df = df.copy()
    df["_rank"] = df["label_source"].map(_rank_of)
    df["occurred_at"] = pd.to_datetime(df["occurred_at"], utc=True, errors="coerce")

    rows = []
    for _, grp in df.groupby("comment_hash", sort=False):
        ham = grp[grp.label == 0]
        pool = ham if len(ham) else grp  # ham 覆蓋 spam
        pick = pool.sort_values(["_rank", "occurred_at"], ascending=False).iloc[0]
        rows.append(pick)

    out = pd.DataFrame(rows).drop(columns=["_rank"])
    out["label_weight"] = out["label_source"].map(
        lambda s: RANK_WEIGHT.get(_rank_of(s), 0.5)
    )
    return out.reset_index(drop=True)


def _normalize_l1(df: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame({
        "comment_hash": df["comment_hash"],
        "author_hash": df.get("author_hash"),
        "text": df["content"],
        "label": df["label"].astype(int),
        "label_source": df["label_source"],
        "score": df.get("spam_score"),
        "occurred_at": df["updated_at"],
    })


def _normalize_l2(df: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame({
        "comment_hash": df["commentHash"],
        "author_hash": df.get("authorHash"),
        "text": df["text"],
        "label": df["label"].astype(int),
        "label_source": df["labelSource"],
        "score": df.get("score"),
        "occurred_at": df["occurredAt"],
    })


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--l1-glob", help="local glob or s3 prefix of L1 parquet")
    ap.add_argument("--l2-glob", help="local glob or s3 prefix of L2 jsonl")
    ap.add_argument("--out", required=True, help="output parquet path (local or s3://)")
    args = ap.parse_args()

    import glob
    frames = []
    if args.l1_glob:
        for p in glob.glob(args.l1_glob):
            frames.append(_normalize_l1(pd.read_parquet(p)))
    if args.l2_glob:
        for p in glob.glob(args.l2_glob):
            frames.append(_normalize_l2(pd.read_json(p, lines=True)))
    if not frames:
        print("no inputs matched")
        return 1

    merged = pd.concat(frames, ignore_index=True)
    clean = resolve(merged)
    print(f"in={len(merged)} unique_comments={len(clean)} "
          f"spam={int((clean.label==1).sum())} ham={int((clean.label==0).sum())}")
    clean.to_parquet(args.out, index=False)
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
