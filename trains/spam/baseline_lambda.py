"""Measure the CURRENT spam model baseline on Community Watch comment labels.

Source (per project decision): score the harvested labels with the ONLINE spam
Lambda (the model currently in production), then compare against the Community
Watch ground truth to get precision / recall / false-positive (over-kill) rate.

The Lambda contract (see spam/app.py): POST the comment body as the request body;
response is JSON {"score": <float in 0..1>}. app.py runs html2text + chunking and
returns max(scores). We send the raw stored content so scoring matches production.

Holdout
-------
Generalization is evaluated by GROUPING on template_family (content_hash) so the
metric reflects performance on UNSEEN templates rather than memorizing the few
bot-account templates that dominate the corpus (the report notes 70% of spam came
from 4 accounts / a single reporter did ~90% of removals). A family appears wholly
in train OR wholly in holdout, never split.

Usage
-----
    export SPAM_LAMBDA_URL=https://<api-id>.execute-api.<region>.amazonaws.com/spam
    export SPAM_LAMBDA_KEY=...        # optional x-api-key
    python baseline_lambda.py --labels community-watch-comment-labels.parquet.gzip \
        --threshold 0.5 --holdout-frac 0.3

Outputs a metrics table at the configured threshold plus a threshold sweep, for
both the full set and the held-out (unseen-family) set.
"""
import argparse
import os
import sys
import time

import pandas as pd
import requests

LAMBDA_URL = os.environ.get("SPAM_LAMBDA_URL")
LAMBDA_KEY = os.environ.get("SPAM_LAMBDA_KEY")


def score_one(session, text):
    headers = {"Content-Type": "text/plain"}
    if LAMBDA_KEY:
        headers["x-api-key"] = LAMBDA_KEY
    # Retry on BOTH HTTP 5xx and connection-level errors: under sustained
    # sequential load the API Gateway / Lambda resets the socket
    # (ConnectionResetError 54). Without catching it, one reset aborts the run.
    last_err = None
    for delay in [0, 2, 6, 15, 30]:
        if delay:
            time.sleep(delay)
        try:
            resp = session.post(LAMBDA_URL, data=(text or "").encode("utf-8"),
                                headers=headers, timeout=90)
        except requests.exceptions.RequestException as e:
            last_err = e
            continue
        if 500 <= resp.status_code < 600:
            last_err = RuntimeError(f"HTTP {resp.status_code}")
            continue
        return float(resp.json()["score"])
    raise RuntimeError(f"scoring failed for text len={len(text or '')}: {last_err}")


def metrics(df, threshold):
    """Binary metrics treating score >= threshold as predicted-spam."""
    pred = df["score"] >= threshold
    actual = df["is_spam"] == 1
    tp = int((pred & actual).sum())
    fp = int((pred & ~actual).sum())
    fn = int((~pred & actual).sum())
    tn = int((~pred & ~actual).sum())
    precision = tp / (tp + fp) if (tp + fp) else float("nan")
    recall = tp / (tp + fn) if (tp + fn) else float("nan")
    # over-kill rate = FP / actual-negatives (the metric the report optimizes against)
    fp_rate = fp / (fp + tn) if (fp + tn) else float("nan")
    return {"n": len(df), "tp": tp, "fp": fp, "fn": fn, "tn": tn,
            "precision": round(precision, 4), "recall": round(recall, 4),
            "fp_rate(over-kill)": round(fp_rate, 4)}


def family_holdout(df, frac, seed=42):
    """Split so each template_family is entirely in train or holdout."""
    fams = df["template_family"].dropna().drop_duplicates().sample(frac=1.0, random_state=seed)
    n_holdout = int(len(fams) * frac)
    holdout_fams = set(fams.iloc[:n_holdout])
    is_holdout = df["template_family"].isin(holdout_fams)
    return df[~is_holdout], df[is_holdout]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--labels", default="community-watch-comment-labels.parquet.gzip")
    ap.add_argument("--threshold", type=float, default=0.5)
    ap.add_argument("--holdout-frac", type=float, default=0.3)
    ap.add_argument("--out", default="baseline-metrics.csv")
    ap.add_argument("--workers", type=int, default=6)
    args = ap.parse_args()

    if not LAMBDA_URL:
        sys.exit("Set SPAM_LAMBDA_URL to the online spam Lambda endpoint.")

    df = pd.read_parquet(args.labels)
    df = df[df["text_available"]].copy()  # can only score rows with text
    if df.empty:
        sys.exit("No rows with available text; re-run harvest before content expiry.")
    print(f"Scoring {len(df)} comments via {LAMBDA_URL} ...", file=sys.stderr)

    # Resumable cache keyed by uuid: a mid-run reset never re-scores from zero.
    cache_path = args.out + ".scores.json"
    cache = {}
    if os.path.exists(cache_path):
        import json
        with open(cache_path) as fh:
            cache = json.load(fh)
        print(f"Resuming: {len(cache)} cached scores loaded.", file=sys.stderr)

    import json
    from concurrent.futures import ThreadPoolExecutor, as_completed

    todo = [(u, t) for u, t in zip(df["uuid"], df["content"]) if u not in cache]
    print(f"  {len(cache)} cached, {len(todo)} to score "
          f"(workers={args.workers})", file=sys.stderr)
    done = 0
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        # one Session per worker thread is safest; here we let requests handle it
        futs = {ex.submit(score_one, requests.Session(), t): u for u, t in todo}
        for fut in as_completed(futs):
            uuid = futs[fut]
            cache[uuid] = fut.result()
            done += 1
            if done % 25 == 0:
                with open(cache_path, "w") as fh:
                    json.dump(cache, fh)
                print(f"  scored {done}/{len(todo)}", file=sys.stderr)
    with open(cache_path, "w") as fh:
        json.dump(cache, fh)
    df["score"] = [cache[u] for u in df["uuid"]]

    train, holdout = family_holdout(df, args.holdout_frac)

    print(f"\n== Baseline @ threshold={args.threshold} ==")
    print("ALL    :", metrics(df, args.threshold))
    print("HOLDOUT:", metrics(holdout, args.threshold), "(unseen template families)")

    print("\n== Threshold sweep (ALL) ==")
    sweep_rows = []
    for t in [0.3, 0.35, 0.5, 0.7, 0.85, 0.9]:
        m_all = metrics(df, t)
        m_ho = metrics(holdout, t)
        print(f"  t={t}: P={m_all['precision']} R={m_all['recall']} "
              f"FP%={m_all['fp_rate(over-kill)']}  | holdout R={m_ho['recall']}")
        sweep_rows.append({"threshold": t, **{f"all_{k}": v for k, v in m_all.items()},
                           **{f"holdout_{k}": v for k, v in m_ho.items()}})

    pd.DataFrame(sweep_rows).to_csv(args.out, index=False)
    print(f"\nSweep written to {args.out}")
    print("\nNote: this is the BASELINE (current production model). P1 LoRA retrain "
          "must not raise over-kill (fp_rate) above this, while improving recall, "
          "especially on the HOLDOUT (unseen families).")


if __name__ == "__main__":
    main()
