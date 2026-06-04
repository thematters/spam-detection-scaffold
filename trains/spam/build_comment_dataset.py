"""Assemble the comment-level training set: positives (Community Watch) + negatives
(ordinary article comments), with a FAMILY-GROUPED train/holdout split.

Rationale
---------
The old article pipeline used a random 80/20 split, which leaks templated spam
across train/val and inflates the metric. Comment spam is HEAVILY templated
(53 families, top family ~31% of positives), so we split on `template_family`
(content hash): every family lands wholly in train OR wholly in holdout. The
holdout therefore measures generalization to UNSEEN templates -- the metric that
actually matters for catching new spam waves.

Inputs
------
  --positives  community-watch-comment-labels.parquet.gzip  (is_spam mostly 1)
  --negatives  normal-comments.parquet.gzip                 (is_spam 0)

Output: train/holdout parquet with columns text, is_spam, template_family, source.
"""
import argparse

import pandas as pd


def normalize_cols(df):
    """Coerce either harvester's schema to {text, is_spam, template_family}."""
    if "content_text" in df.columns:
        text = df["content_text"]
    elif "content" in df.columns:
        # positives store raw originalContent; strip light HTML the same way
        import re
        tag = re.compile(r"<[^>]+>")
        ws = re.compile(r"\s+")
        text = df["content"].fillna("").map(
            lambda s: ws.sub(" ", tag.sub(" ", s)).strip())
    else:
        raise SystemExit("no content/content_text column")
    out = pd.DataFrame({
        "text": text,
        "is_spam": df["is_spam"].astype(int),
        "template_family": df["template_family"],
    })
    return out[out["text"].str.len() >= 2]


def family_split(df, holdout_frac, seed=42):
    fams = df["template_family"].dropna().drop_duplicates().sample(frac=1.0, random_state=seed)
    n_ho = int(len(fams) * holdout_frac)
    ho = set(fams.iloc[:n_ho])
    mask = df["template_family"].isin(ho)
    return df[~mask].reset_index(drop=True), df[mask].reset_index(drop=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--positives", default="community-watch-comment-labels.parquet.gzip")
    ap.add_argument("--negatives", default="normal-comments.parquet.gzip")
    ap.add_argument("--holdout-frac", type=float, default=0.3)
    ap.add_argument("--out-prefix", default="comment-dataset")
    args = ap.parse_args()

    pos = normalize_cols(pd.read_parquet(args.positives))
    pos = pos[pos["is_spam"] == 1]            # use only confirmed-spam as positives
    pos["source"] = "community_watch"
    neg = normalize_cols(pd.read_parquet(args.negatives))
    neg["source"] = "article_comment"

    # de-dup negatives that accidentally share a family with a positive (rare)
    pos_fams = set(pos["template_family"])
    neg = neg[~neg["template_family"].isin(pos_fams)]

    df = pd.concat([pos, neg], ignore_index=True).drop_duplicates(subset=["text"])
    train, holdout = family_split(df, args.holdout_frac)

    train.to_parquet(f"{args.out_prefix}-train.parquet.gzip", compression="gzip")
    holdout.to_parquet(f"{args.out_prefix}-holdout.parquet.gzip", compression="gzip")

    def bal(d):
        return d["is_spam"].value_counts().to_dict()
    print(f"TOTAL   {len(df)}  balance={bal(df)}  families={df['template_family'].nunique()}")
    print(f"TRAIN   {len(train)}  balance={bal(train)}  families={train['template_family'].nunique()}")
    print(f"HOLDOUT {len(holdout)}  balance={bal(holdout)}  families={holdout['template_family'].nunique()}")
    print(f"  -> {args.out_prefix}-train.parquet.gzip / -holdout.parquet.gzip")


if __name__ == "__main__":
    main()
