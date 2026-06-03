"""Cheap-first comment-spam baselines on a laptop (M2/CPU, $0, no GPU).

We do NOT jump to LoRA fine-tuning. The spam is heavily templated (~50 distinct
removed templates), so two cheap models are evaluated first, both on the
FAMILY-GROUPED holdout (unseen templates) -- the metric that matters:

  Level-0  embedding -> logistic regression (class-weighted)
  Level-1  centroid / kNN similarity to known-spam embeddings
           (this doubles as the coast-guard bot's B2 similarity detector)

Embeddings come from a small multilingual model on CPU/MPS via
sentence-transformers. Default: intfloat/multilingual-e5-small (118M, fast,
strong multilingual). Override with --model (e.g. the project's granite base).

Metrics mirror baseline_lambda.py: precision / recall / over-kill(FP) rate at a
threshold sweep, plus the project's bias = 2*FP + FN selection. Compared against
the production Lambda baseline (recall ~0.68, see plan 11.7).

Usage
-----
    python cheap_baselines.py \
        --train comment-dataset-train.parquet.gzip \
        --holdout comment-dataset-holdout.parquet.gzip \
        --model intfloat/multilingual-e5-small
"""
import argparse
import sys

import numpy as np
import pandas as pd


def embed(model, texts, prefix=""):
    # e5 models expect a "query: " / "passage: " prefix; harmless for others.
    xs = [prefix + t for t in texts] if prefix else list(texts)
    return model.encode(xs, batch_size=64, normalize_embeddings=True,
                        show_progress_bar=False)


def metrics_at(scores, y, t):
    pred = scores >= t
    actual = y == 1
    tp = int((pred & actual).sum()); fp = int((pred & ~actual).sum())
    fn = int((~pred & actual).sum()); tn = int((~pred & ~actual).sum())
    p = tp / (tp + fp) if (tp + fp) else float("nan")
    r = tp / (tp + fn) if (tp + fn) else float("nan")
    fpr = fp / (fp + tn) if (fp + tn) else float("nan")
    return {"t": round(t, 2), "P": round(p, 3), "R": round(r, 3),
            "FP%": round(fpr, 4), "bias(2fp+fn)": 2 * fp + fn,
            "tp": tp, "fp": fp, "fn": fn, "tn": tn}


def sweep(name, scores, y, thresholds=(0.3, 0.5, 0.7, 0.8, 0.85, 0.9, 0.95)):
    print(f"\n== {name} (HOLDOUT, unseen families) ==")
    best = None
    for t in thresholds:
        m = metrics_at(scores, y, t)
        print(f"  {m}")
        if best is None or m["bias(2fp+fn)"] < best["bias(2fp+fn)"]:
            best = m
    print(f"  -> best-by-bias: t={best['t']} P={best['P']} R={best['R']} FP%={best['FP%']}")
    return best


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--train", default="comment-dataset-train.parquet.gzip")
    ap.add_argument("--holdout", default="comment-dataset-holdout.parquet.gzip")
    ap.add_argument("--model", default="intfloat/multilingual-e5-small")
    args = ap.parse_args()

    from sentence_transformers import SentenceTransformer
    from sklearn.linear_model import LogisticRegression

    tr = pd.read_parquet(args.train)
    ho = pd.read_parquet(args.holdout)
    print(f"train {len(tr)} (spam {int((tr.is_spam==1).sum())}) | "
          f"holdout {len(ho)} (spam {int((ho.is_spam==1).sum())}) | model {args.model}",
          file=sys.stderr)

    model = SentenceTransformer(args.model)
    Xtr = embed(model, tr["text"].tolist(), prefix="query: ")
    Xho = embed(model, ho["text"].tolist(), prefix="query: ")
    ytr = tr["is_spam"].to_numpy(); yho = ho["is_spam"].to_numpy()

    # ---- Level-0: embedding -> logistic regression (class-weighted) ----
    clf = LogisticRegression(max_iter=2000, class_weight="balanced", C=1.0)
    clf.fit(Xtr, ytr)
    s_lr = clf.predict_proba(Xho)[:, 1]
    best_lr = sweep("Level-0  embedding + logreg", s_lr, yho)

    # ---- Level-1: cosine similarity to known-spam centroid + max-kNN ----
    spam_vecs = Xtr[ytr == 1]
    centroid = spam_vecs.mean(axis=0)
    centroid /= (np.linalg.norm(centroid) + 1e-9)
    s_cent = Xho @ centroid                      # cosine (vectors already L2-normed)
    s_knn = (Xho @ spam_vecs.T).max(axis=1)      # nearest known-spam similarity
    best_cent = sweep("Level-1a spam-centroid cosine", s_cent, yho)
    best_knn = sweep("Level-1b max kNN-to-spam cosine", s_knn, yho)

    print("\n=== SUMMARY (holdout, unseen templates) vs production Lambda R~0.678 ===")
    for name, b in [("logreg", best_lr), ("centroid", best_cent), ("maxkNN", best_knn)]:
        print(f"  {name:9s} best-by-bias  P={b['P']}  R={b['R']}  FP%={b['FP%']}  "
              f"@t={b['t']}  (fp={b['fp']} fn={b['fn']})")
    print("\nNote: holdout has few positives (templates are scarce); treat R as coarse. "
          "kNN/centroid IS the bot's B2 detector, so a strong result here unifies "
          "model + bot detection. Escalate to LoRA only if all three trail here.")


if __name__ == "__main__":
    main()
