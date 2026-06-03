"""Data-driven threshold calibration for the coast-guard bot's two tiers.

We need two cutoffs on the comment-spam model's P(spam):
  Tier-2 (打掃移除): removals are irreversible-ish and public -> demand ~zero
                     over-kill (false removals of benign comments).
  Tier-1 (檢舉):     reports are reviewable and need 3 distinct reporters to
                     auto-collapse, so a little more over-kill is tolerable.

Honest scores: we do GROUP K-FOLD with group = template_family, so every family
is scored by a model that never saw it (leakage-free, mirrors deployment on new
templates). We then sweep thresholds on the out-of-fold scores.

Note on base rate: precision in production depends on the spam:benign ratio of
the live stream (spam is rare there), which differs from this dataset's ratio.
The OVER-KILL rate (FP / benign) is prevalence-independent, so we calibrate the
tiers against over-kill targets, and report recall achieved at each.

Usage
-----
    python calibrate_thresholds.py --model intfloat/multilingual-e5-small
"""
import argparse
import re

import numpy as np
import pandas as pd

TAG = re.compile(r"<[^>]+>"); WS = re.compile(r"\s+")


def strip(s):
    return WS.sub(" ", TAG.sub(" ", s or "")).strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--positives", default="community-watch-comment-labels.parquet.gzip")
    ap.add_argument("--negatives", default="normal-comments.parquet.gzip")
    ap.add_argument("--model", default="intfloat/multilingual-e5-small")
    ap.add_argument("--prefix", default="query: ")
    ap.add_argument("--folds", type=int, default=5)
    args = ap.parse_args()

    from sentence_transformers import SentenceTransformer
    from sklearn.linear_model import LogisticRegression
    from sklearn.model_selection import GroupKFold

    pos = pd.read_parquet(args.positives)
    pos = pos[pos["is_spam"] == 1].copy()
    pos["text"] = pos["content"].fillna("").map(strip)
    pos = pos[pos["text"].str.len() >= 2].drop_duplicates(subset=["text"])
    pos["fam"] = pos["template_family"]
    neg = pd.read_parquet(args.negatives)
    neg["text"] = neg["content_text"] if "content_text" in neg else neg["content"].map(strip)
    neg = neg[neg["text"].str.len() >= 2].drop_duplicates(subset=["text"])
    neg["fam"] = neg["template_family"]

    df = pd.concat([
        pos[["text", "fam"]].assign(y=1),
        neg[["text", "fam"]].assign(y=0),
    ], ignore_index=True)

    st = SentenceTransformer(args.model)
    X = st.encode([args.prefix + t for t in df["text"]], normalize_embeddings=True,
                  batch_size=64, show_progress_bar=False)
    y = df["y"].to_numpy()
    groups = df["fam"].astype("category").cat.codes.to_numpy()

    # leakage-free out-of-fold scores (family never in both train and test)
    oof = np.zeros(len(df))
    gkf = GroupKFold(n_splits=args.folds)
    for tr, te in gkf.split(X, y, groups):
        clf = LogisticRegression(max_iter=5000, class_weight="balanced")
        clf.fit(X[tr], y[tr])
        oof[te] = clf.predict_proba(X[te])[:, 1]

    pos_s = oof[y == 1]; neg_s = oof[y == 0]
    n_pos, n_neg = len(pos_s), len(neg_s)
    print(f"OOF scores: {n_pos} spam, {n_neg} benign ({args.model})\n")

    grid = [round(t, 2) for t in np.arange(0.30, 1.00, 0.05)]
    print(f"{'t':>5} {'recall':>7} {'over-kill':>10} {'FP#':>4} {'prec@dataset':>13}")
    rows = []
    for t in grid:
        tp = int((pos_s >= t).sum()); fn = n_pos - tp
        fp = int((neg_s >= t).sum()); tn = n_neg - fp
        recall = tp / n_pos
        fpr = fp / n_neg
        prec = tp / (tp + fp) if (tp + fp) else float("nan")
        rows.append((t, recall, fpr, fp, prec))
        print(f"{t:>5} {recall:>7.3f} {fpr:>10.4f} {fp:>4} {prec:>13.3f}")

    def pick(max_fpr):
        # lowest threshold whose over-kill <= max_fpr (maximizes recall under bound)
        cands = [r for r in rows if r[2] <= max_fpr]
        return min(cands, key=lambda r: r[0]) if cands else None

    # Tier-2: zero observed over-kill on the benign set; Tier-1: <=1% over-kill.
    t2 = pick(0.0)
    t1 = pick(0.01)
    print("\n=== recommended ===")
    if t2:
        print(f"  Tier-2 (remove) t>={t2[0]:.2f}  recall={t2[1]:.3f}  over-kill={t2[2]:.4f} (0 FP / {n_neg} benign)")
    if t1:
        print(f"  Tier-1 (report) t>={t1[0]:.2f}  recall={t1[1]:.3f}  over-kill={t1[2]:.4f} ({t1[3]} FP / {n_neg})")
    print(f"\n  benign score: p50={np.percentile(neg_s,50):.3f} p99={np.percentile(neg_s,99):.3f} "
          f"max={neg_s.max():.3f}")
    print(f"  spam score:   p10={np.percentile(pos_s,10):.3f} p50={np.percentile(pos_s,50):.3f}")
    print("\n  NOTE base rate: production is mostly benign, so live precision >> "
          "the dataset-precision column. Over-kill (FP/benign) is the safe bound.")


if __name__ == "__main__":
    main()
