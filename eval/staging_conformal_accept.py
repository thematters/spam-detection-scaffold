#!/usr/bin/env python3
"""軸一 B 驗收：對已部署的 staging conformal endpoint 跑標註樣本，量測決策行為。

對每筆樣本 POST endpoint，讀回 {score, decision}，依真實 label 統計：
  ham (is_spam=0):  block 率(=誤殺)、review 率、allow 率
  spam(is_spam=1):  block 率(=recall)、review 率、allow 率

⚠️ 用 labels 訓練集 = IN-SAMPLE，數字偏樂觀（與 calib 同源）。本腳本的目的是驗證
   「部署的 endpoint 決策接線正確 + calib 生效」，非泛化數字。真正泛化需用訓練截點
   之後的 held-out 文章（read-replica 撈 article_id 更大者）。

⚠️ 2026-06-13 首跑發現（staging, 100+100, eps=0.02）：ham block≈97%、spam recall=100%。
   97% 誤殺與 calib 自身 LOO 1.8% 嚴重矛盾，原因是「訓練集 ham 標籤雜訊」——隨機抽到的
   ham 含大量「幾乎只有 <figure><img> 無文字」的文章，granite 對低文字文章給≈1.0。
   故此數字【不可採信為系統誤殺率】。正式驗收前提（缺一不可）：
     1. 用 read-replica 撈訓練截點後、人工確認過的乾淨 held-out ham（非 in-sample 訓練標籤）；
     2. 過濾/分層「低文字/圖片型」文章（與線上實際送檢的文字分布一致）；
     3. 控制並發（≤2~3）避免 504 冷啟動污染（首跑 28/200 是 504）。
   結論：軸一 B 部署 OK + tar 已驗證，但 conformal「驗收」尚未通過，不可據此上 prod。

用法：
  python staging_conformal_accept.py \
    --parquet s3://spam-detection-model/spam-labels-...parquet.gzip \
    --endpoint https://fjrmugbg5j.execute-api.ap-southeast-1.amazonaws.com/Prod/spam/infer/ \
    --ham 150 --spam 150 --concurrency 8 --seed 42
"""
from __future__ import annotations

import argparse
import json
import os
import tempfile
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed


def load_sample(parquet: str, n_ham: int, n_spam: int, seed: int):
    import pandas as pd

    path = parquet
    if parquet.startswith("s3://"):
        import boto3

        b, k = parquet[5:].split("/", 1)
        fd, path = tempfile.mkstemp(suffix=".parquet.gzip")
        os.close(fd)
        print(f"downloading {parquet} ...")
        boto3.client("s3").download_file(b, k, path)

    # column projection keeps memory bounded
    df = pd.read_parquet(path, columns=["content", "is_spam"])
    df = df.dropna(subset=["content"])
    ham = df[df.is_spam == 0].sample(min(n_ham, (df.is_spam == 0).sum()), random_state=seed)
    spam = df[df.is_spam == 1].sample(min(n_spam, (df.is_spam == 1).sum()), random_state=seed)
    print(f"sampled ham={len(ham)} spam={len(spam)}")
    return ([(t, 0, None, "") for t in ham.content]
            + [(t, 1, None, "") for t in spam.content])


def load_sample_replica(dsn: str, n_ham: int, n_spam: int, cutoff_id: int,
                        min_text_len: int, seed: int):
    """正式驗收用：從 read-replica 撈訓練截點（id>cutoff）之後的乾淨 held-out。
      ham  = 截點後、active、作者未受限、文字長度達標（濾掉純圖/低文字）= 真正合法樣本。
      spam = 截點後、作者受限（小黑屋）的文章 = spam 代理正樣本。
    全程唯讀 SELECT。需 psycopg(v3)。"""
    import psycopg

    body = ("regexp_replace(coalesce(av.title,'') || ' ' || coalesce(ac.content,''), "
            "'<[^>]+>', ' ', 'g')")
    sql_ham = f"""
        SELECT a.id, av.title, {body} AS content
        FROM article a
        JOIN article_version_newest av ON av.article_id = a.id
        JOIN article_content ac        ON ac.id = av.content_id
        WHERE a.id > %(cutoff)s AND a.state = 'active'
          AND NOT EXISTS (SELECT 1 FROM user_restriction ur WHERE ur.user_id = a.author_id)
          AND length({body}) >= %(minlen)s
        ORDER BY md5(a.id::text || %(seed)s) LIMIT %(n)s"""
    sql_spam = f"""
        SELECT a.id, av.title, {body} AS content
        FROM article a
        JOIN article_version_newest av ON av.article_id = a.id
        JOIN article_content ac        ON ac.id = av.content_id
        JOIN user_restriction ur       ON ur.user_id = a.author_id
        WHERE a.id > %(cutoff)s
        ORDER BY md5(a.id::text || %(seed)s) LIMIT %(n)s"""
    out = []
    with psycopg.connect(dsn, connect_timeout=20) as conn:
        with conn.cursor() as cur:
            cur.execute(sql_ham, {"cutoff": cutoff_id, "minlen": min_text_len,
                                  "seed": str(seed), "n": n_ham})
            out += [(r[2], 0, r[0], r[1]) for r in cur.fetchall()]   # (content, label, id, title)
            cur.execute(sql_spam, {"cutoff": cutoff_id, "seed": str(seed), "n": n_spam})
            out += [(r[2], 1, r[0], r[1]) for r in cur.fetchall()]
    n0 = sum(1 for x in out if x[1] == 0)
    print(f"replica held-out: ham={n0} spam={len(out) - n0} (id>{cutoff_id}, text≥{min_text_len})")
    return out


def score(endpoint: str, text: str):
    # 文章 endpoint 吃 RAW body（app.py 對 body 直接 _split_group_lines）。
    # ⚠️ 勿送 JSON {"text":...}——實測那會讓它把 JSON 字串當文章評分→恆回 score≈1.0→全部 block
    #    （2026-06-15 假象「100% 誤殺」的根因）。
    req = urllib.request.Request(endpoint, data=text.encode("utf-8"), method="POST")
    r = json.load(urllib.request.urlopen(req, timeout=120))
    return r.get("score"), r.get("decision")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", choices=["parquet", "replica"], default="parquet",
                    help="parquet=in-sample 偏樂觀；replica=正式驗收（截點後乾淨 held-out）")
    ap.add_argument("--parquet", help="--source parquet 時必填（本地或 s3://）")
    ap.add_argument("--dsn-env", default="PG_DSN", help="--source replica 時，存 DSN 的環境變數名")
    ap.add_argument("--cutoff-id", type=int, default=1104414, help="訓練截點 article.id（v20251229）")
    ap.add_argument("--min-text-len", type=int, default=200, help="ham 最少文字長度，濾純圖/低文字")
    ap.add_argument("--endpoint", required=True)
    ap.add_argument("--ham", type=int, default=150)
    ap.add_argument("--spam", type=int, default=150)
    ap.add_argument("--concurrency", type=int, default=3,
                    help="預設低並發避免 endpoint 504 冷啟動污染")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", help="把結果 JSON 寫到此路徑（CodeBuild 上傳 S3 用）")
    args = ap.parse_args()

    if args.source == "replica":
        dsn = os.environ.get(args.dsn_env)
        if not dsn:
            print(f"missing DSN env {args.dsn_env}"); return 2
        samples = load_sample_replica(dsn, args.ham, args.spam,
                                      args.cutoff_id, args.min_text_len, args.seed)
    else:
        if not args.parquet:
            print("--source parquet requires --parquet"); return 2
        samples = load_sample(args.parquet, args.ham, args.spam, args.seed)

    by_label = {0: Counter(), 1: Counter()}
    errors = 0
    misfires = []   # 真 ham 卻被 block(誤殺) / review(送人工) 的個案，給人工檢視

    def work(item):
        text, label, aid, title = item
        sc, decision = score(args.endpoint, text)
        return label, decision, sc, aid, title, text

    with ThreadPoolExecutor(max_workers=args.concurrency) as ex:
        futs = [ex.submit(work, s) for s in samples]
        for f in as_completed(futs):
            try:
                label, decision, sc, aid, title, text = f.result()
                by_label[label][decision or "error"] += 1
                if label == 0 and decision in ("block", "review"):
                    misfires.append({"decision": decision, "score": sc,
                                     "article_id": aid, "title": (title or "")[:80],
                                     "snippet": " ".join((text or "").split())[:200]})
            except Exception as e:  # noqa: BLE001
                errors += 1
                print(f"  err: {str(e)[:120]}")

    tag = "REPLICA held-out（正式驗收）" if args.source == "replica" else "IN-SAMPLE, optimistic"
    print(f"\n=== staging conformal acceptance ({tag}) ===")
    result = {"source": args.source, "endpoint": args.endpoint, "errors": errors, "by_label": {}}
    for label, name in [(0, "ham"), (1, "spam")]:
        c = by_label[label]
        tot = sum(c.values()) or 1
        block, review, allow = (c.get(k, 0) / tot for k in ("block", "review", "allow"))
        print(f"{name:>4}: n={sum(c.values())} block={block:.1%} review={review:.1%} "
              f"allow={allow:.1%}  raw={dict(c)}")
        result["by_label"][name] = {"n": sum(c.values()), "block": block,
                                    "review": review, "allow": allow, "raw": dict(c)}
    print(f"errors={errors}")
    print("note: ham block% = 誤殺；spam block% = recall；review% = 進人工佇列")

    # 誤殺(block)個案優先、其次 review；附 id/標題/分數/片段供人工判斷是真誤殺還是標錯的 spam
    misfires.sort(key=lambda m: (m["decision"] != "block", -(m["score"] or 0)))
    result["misfires"] = misfires
    blocked = [m for m in misfires if m["decision"] == "block"]
    print(f"\n=== 真 ham 被誤判個案：block(誤殺)={len(blocked)} review={len(misfires) - len(blocked)} ===")
    for m in misfires[:25]:
        print(f"[{m['decision']:>6}] score={m['score']} id={m['article_id']} «{m['title']}»")
        print(f"         {m['snippet']}")
    if args.out:
        with open(args.out, "w") as fh:
            json.dump(result, fh, ensure_ascii=False, indent=2)
        print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
