"""Train the FINAL comment-spam model and export a deployable artifact.

Model = e5-small embedding (frozen) + logistic-regression head. Chosen over LoRA
because a cheap embedding+logreg already beats the production model on unseen
templates (leave-one-family-out recall 0.88 vs 0.68, over-kill 0.33%; see
cv_eval.py / plan 11.8).

We train on ALL available data (every unique spam template + all negatives) --
generalization was already validated by LOFO CV, so the deployed model should use
the full signal. The artifact mirrors the existing serving contract: everything
lands under ./model/ inside a tar referenced by SpamModelTarUrl.

Artifact layout (./model/ in the tar)
-------------------------------------
  model/                      <- SentenceTransformer.save() of e5-small (offline)
  model/head.json            <- {coef, intercept, base_prefix, threshold, dim}

Serving (infer.py) loads the SentenceTransformer from ./model/ and applies the
logreg head with plain numpy (no sklearn at runtime).

Usage
-----
    python train_comment_head.py \
        --positives community-watch-comment-labels.parquet.gzip \
        --negatives normal-comments.parquet.gzip \
        --model intfloat/multilingual-e5-small \
        --out-dir build_model --tar spam_comment_e5.tar
"""
import argparse
import json
import os
import re
import subprocess
import sys

import numpy as np
import pandas as pd

TAG = re.compile(r"<[^>]+>")
WS = re.compile(r"\s+")


def strip(s):
    return WS.sub(" ", TAG.sub(" ", s or "")).strip()


def load_texts(positives, negatives):
    pos = pd.read_parquet(positives)
    pos = pos[pos["is_spam"] == 1].copy()
    pos["text"] = pos["content"].fillna("").map(strip)
    pos = pos[pos["text"].str.len() >= 2].drop_duplicates(subset=["text"])
    neg = pd.read_parquet(negatives)
    neg["text"] = neg["content_text"] if "content_text" in neg else neg["content"].map(strip)
    neg = neg[neg["text"].str.len() >= 2].drop_duplicates(subset=["text"])
    X = pd.concat([pos["text"], neg["text"]], ignore_index=True)
    y = np.r_[np.ones(len(pos)), np.zeros(len(neg))]
    return X.tolist(), y, len(pos), len(neg)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--positives", default="community-watch-comment-labels.parquet.gzip")
    ap.add_argument("--negatives", default="normal-comments.parquet.gzip")
    ap.add_argument("--model", default="intfloat/multilingual-e5-small")
    ap.add_argument("--prefix", default="query: ")
    ap.add_argument("--threshold", type=float, default=0.7,
                    help="recommended decision threshold (consumers may override)")
    ap.add_argument("--out-dir", default="build_model")
    ap.add_argument("--tar", default="spam_comment_e5.tar")
    args = ap.parse_args()

    from sentence_transformers import SentenceTransformer
    from sklearn.linear_model import LogisticRegression

    texts, y, npos, nneg = load_texts(args.positives, args.negatives)
    print(f"Training on {npos} unique spam + {nneg} negatives | base {args.model}",
          file=sys.stderr)

    st = SentenceTransformer(args.model)
    X = st.encode([args.prefix + t for t in texts], normalize_embeddings=True,
                  batch_size=64, show_progress_bar=False)
    clf = LogisticRegression(max_iter=5000, class_weight="balanced", C=1.0)
    clf.fit(X, y)

    # training-fit report (not generalization -- see cv_eval.py for that)
    p = clf.predict_proba(X)[:, 1]
    pred = p >= args.threshold
    tp = int((pred & (y == 1)).sum()); fp = int((pred & (y == 0)).sum())
    fn = int((~pred & (y == 1)).sum())
    print(f"  train-fit @t={args.threshold}: tp={tp} fp={fp} fn={fn} "
          f"(generalization is the LOFO 0.88, not this)", file=sys.stderr)

    # Save DIRECTLY into out_dir so the tar's top level holds the model files
    # (the Dockerfile extracts into ./model/, so top-level files become
    # ./model/<files> -- matching infer.py's CHECKPOINT="./model/").
    model_dir = args.out_dir
    os.makedirs(model_dir, exist_ok=True)
    st.save(model_dir)
    head = {
        "base_prefix": args.prefix,
        "threshold": args.threshold,
        "dim": int(X.shape[1]),
        "coef": clf.coef_[0].tolist(),
        "intercept": float(clf.intercept_[0]),
        "labels": ["benign", "spam"],
        "note": "score = sigmoid(coef.x + intercept) on L2-normalized e5 embedding "
                "of (prefix + html-stripped text)",
    }
    with open(os.path.join(model_dir, "head.json"), "w") as fh:
        json.dump(head, fh)

    # tar the CONTENTS of out-dir so it extracts to ./model/ (matches Dockerfile)
    subprocess.run(["tar", "cf", args.tar, "-C", args.out_dir, "."], check=True)
    size = os.path.getsize(args.tar) / 1e6
    print(f"\nWrote {args.tar} ({size:.1f} MB). Upload to S3/HTTPS, then:")
    print(f"  sam build --parameter-overrides SpamModelTarUrl=<url> LambdaRoleArn=<arn>")
    print(f"  sam deploy")


if __name__ == "__main__":
    main()
