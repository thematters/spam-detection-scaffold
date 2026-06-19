#!/usr/bin/env python3
"""用訓練好的動態模型（e5-small 凍結 + logreg head）對 jsonl 打分。

獨立於 spam/infer.py（那支在 import 時就寫死載入 ./model/），這裡可指定 --model-dir，
給雲端 score job（VPC runner train-moment 同環境）對 held-out 打分用。

輸入 jsonl：每行 {"id":..., "content":..., "label":0/1?}（label 可省）。
輸出 jsonl：每行 {"id":..., "score": P(spam), "label":...}。

用法：python score_moment.py --model-dir ./model --in heldout.jsonl --out scored.jsonl
"""
from __future__ import annotations
import argparse
import json
import math

import numpy as np
from sentence_transformers import SentenceTransformer


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model-dir", required=True, help="解開的模型目錄（含 head.json 與 ST 檔）")
    ap.add_argument("--in", dest="inp", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--batch-size", type=int, default=64)
    args = ap.parse_args()

    md = args.model_dir.rstrip("/") + "/"
    model = SentenceTransformer(md)
    model.max_seq_length = 512
    head = json.load(open(md + "head.json"))
    coef = np.asarray(head["coef"], dtype="float32")
    intercept = float(head["intercept"])
    prefix = head.get("base_prefix", "")

    rows = [json.loads(l) for l in open(args.inp) if l.strip()]
    texts = [prefix + (r.get("content") or "") for r in rows]
    embs = model.encode(texts, normalize_embeddings=True, batch_size=args.batch_size)
    logits = embs @ coef + intercept
    with open(args.out, "w") as fh:
        for r, z in zip(rows, np.atleast_1d(logits)):
            p = 1.0 / (1.0 + math.exp(-float(z)))
            fh.write(json.dumps({"id": r.get("id"), "score": p,
                                 "label": r.get("label")}, ensure_ascii=False) + "\n")
    print(f"scored {len(rows)} → {args.out}  (threshold 建議 {head.get('threshold', 0.5)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
