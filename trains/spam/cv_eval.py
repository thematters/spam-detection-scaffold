"""Robust unseen-template evaluation via LEAVE-ONE-FAMILY-OUT CV.

The single 30% holdout has only ~11 spam positives, so recall there is coarse.
Here we embed the FULL comment set once, then for EACH spam family: hold that
family out, train logreg on (all other spam families + all negatives), and check
whether the held-out family's comments are caught. Aggregated over all families
this is a far more stable estimate of recall on UNSEEN templates -- exactly what
matters for catching new spam waves. Over-kill (FP%) is measured on a fixed
negative holdout slice each fold.

$0, CPU, seconds. Reuses the same embedding model as cheap_baselines.py.
"""
import argparse

import numpy as np
import pandas as pd


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--positives", default="community-watch-comment-labels.parquet.gzip")
    ap.add_argument("--negatives", default="normal-comments.parquet.gzip")
    ap.add_argument("--model", default="ibm-granite/granite-embedding-107m-multilingual")
    ap.add_argument("--threshold", type=float, default=0.7)
    ap.add_argument("--neg-holdout", type=int, default=300)
    args = ap.parse_args()

    import re
    from sentence_transformers import SentenceTransformer
    from sklearn.linear_model import LogisticRegression

    tag = re.compile(r"<[^>]+>"); ws = re.compile(r"\s+")
    def strip(s): return ws.sub(" ", tag.sub(" ", s or "")).strip()

    pos = pd.read_parquet(args.positives)
    pos = pos[pos["is_spam"] == 1].copy()
    pos["text"] = pos["content"].fillna("").map(strip)
    pos = pos[pos["text"].str.len() >= 2].drop_duplicates(subset=["text"])
    neg = pd.read_parquet(args.negatives)
    neg["text"] = neg["content_text"]

    model = SentenceTransformer(args.model)
    print(f"Embedding {len(pos)} unique spam + {len(neg)} negatives with {args.model} ...")
    Ppos = model.encode(("query: " + pos["text"]).tolist(), normalize_embeddings=True,
                        batch_size=64, show_progress_bar=False)
    Pneg = model.encode(("query: " + neg["text"]).tolist(), normalize_embeddings=True,
                        batch_size=64, show_progress_bar=False)
    fams = pos["template_family"].to_numpy()
    uniq = pd.unique(fams)

    # fixed negative holdout for FP measurement
    rng = np.random.RandomState(42)
    neg_idx = rng.permutation(len(Pneg))
    neg_ho, neg_tr = neg_idx[:args.neg_holdout], neg_idx[args.neg_holdout:]

    caught, total, fp_rates = 0, 0, []
    per_fam = []
    for fam in uniq:
        is_fam = fams == fam
        Xtr = np.vstack([Ppos[~is_fam], Pneg[neg_tr]])
        ytr = np.r_[np.ones((~is_fam).sum()), np.zeros(len(neg_tr))]
        if ytr.sum() == 0:
            continue
        clf = LogisticRegression(max_iter=2000, class_weight="balanced")
        clf.fit(Xtr, ytr)
        # recall on the held-out family
        s_fam = clf.predict_proba(Ppos[is_fam])[:, 1]
        c = int((s_fam >= args.threshold).sum()); n = int(is_fam.sum())
        caught += c; total += n
        per_fam.append((str(fam)[:10], n, round(float(s_fam.mean()), 3), c))
        # FP on fixed neg holdout
        s_neg = clf.predict_proba(Pneg[neg_ho])[:, 1]
        fp_rates.append(float((s_neg >= args.threshold).mean()))

    print(f"\n=== Leave-one-family-out @ t={args.threshold} (model {args.model}) ===")
    print(f"  spam families evaluated: {len(per_fam)}")
    print(f"  UNSEEN-template recall (comments): {caught}/{total} = {caught/total:.3f}")
    print(f"  mean over-kill FP% on held neg:    {np.mean(fp_rates):.4f} "
          f"(max {np.max(fp_rates):.4f})")
    print(f"  vs production Lambda recall ~0.678\n")
    miss = [r for r in per_fam if r[3] < r[1]]
    print(f"  families with any miss: {len(miss)} / {len(per_fam)}")
    for r in sorted(miss, key=lambda x: x[2])[:10]:
        print(f"    family={r[0]} size={r[1]} mean_score={r[2]} caught={r[3]}")


if __name__ == "__main__":
    main()
