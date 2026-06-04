"""Harvest comment-level spam labels from the production Community Watch board.

Source (per project decision): production `community_watch_action` via the
PUBLIC GraphQL query `communityWatchActions` on https://server.matters.town/graphql.

Why this exists
---------------
The current label pipeline (`0.1-merge-labels.ipynb`) is ARTICLE-level only
(article_id/title/content/is_spam). The comment spam model must be trained on
COMMENT-level data, and the Community Watch removal log is our ground truth:
  - reviewState == upheld                  -> positive (spam)
  - actionState in {restored, voided}      -> negative (false positive / cleared)
  - reviewState == reversed                -> negative (false positive)
  - reason_adjusted                        -> keep, relabel reason
  - active + not reversed                  -> positive (provisional)

Pipeline constraint
-------------------
`originalContent` is cleared `contentExpiresAt` (7 days) by
`clearCommunityWatchOriginalContent`. Run this regularly to SNAPSHOT text before
it expires. `contentHash` (SHA-256 of normalized content) is always available and
is used as the template-family key for stratified holdout and clustering, even
after text is cleared.

Auth
----
The query is public, but `originalContent` may be masked on the public path.
Set MATTERS_ACCESS_TOKEN (a Community Watch / admin x-access-token) to attempt
unmasked text. Rows whose text is unavailable are still saved (hash-only),
flagged with text_available=False.

Usage
-----
    export MATTERS_API=https://server.matters.town/graphql   # production
    export MATTERS_ACCESS_TOKEN=...        # optional, for unmasked originalContent
    python harvest_community_watch.py --out community-watch-comment-labels.parquet.gzip

Output columns: uuid, comment_id, source_type, reason, action_state, appeal_state,
review_state, content_hash, content, text_available, report_synced, created_at,
is_spam, template_family
"""
import argparse
import os
import sys
import time

import pandas as pd
import requests

MATTERS_API = os.environ.get("MATTERS_API", "https://server.matters.town/graphql")
ACCESS_TOKEN = os.environ.get("MATTERS_ACCESS_TOKEN")
PAGE_SIZE = 50  # MAX_PUBLIC_ACTIONS_PER_PAGE in matters-server

QUERY = """
query Harvest($input: CommunityWatchActionsInput!) {
  communityWatchActions(input: $input) {
    totalCount
    pageInfo { endCursor hasNextPage }
    edges {
      cursor
      node {
        uuid
        commentId
        sourceType
        reason
        actionState
        appealState
        reviewState
        contentHash
        originalContent
        contentCleared
        reportSynced
        createdAt
      }
    }
  }
}
"""


def _gql(session, query, variables):
    headers = {"Content-Type": "application/json", "x-client-name": "spam-label-harvest"}
    if ACCESS_TOKEN:
        headers["x-access-token"] = ACCESS_TOKEN
    for attempt, delay in enumerate([0, 3, 10]):
        if delay:
            time.sleep(delay)
        resp = session.post(MATTERS_API, json={"query": query, "variables": variables},
                            headers=headers, timeout=60)
        if 500 <= resp.status_code < 600:
            print(f"  {resp.status_code}, retry {attempt + 1}/3", file=sys.stderr)
            continue
        body = resp.json()
        if body.get("errors"):
            raise RuntimeError(f"GraphQL error: {body['errors']}")
        return body["data"]
    raise RuntimeError("failed after 3 attempts")


def label(action_state, review_state):
    """Map Community Watch states to a binary spam label (see module docstring)."""
    if review_state == "reversed" or action_state in ("restored", "voided"):
        return 0
    return 1  # upheld, reason_adjusted, or active-and-not-reversed


def harvest():
    session = requests.Session()
    rows, after, total = [], None, None
    while True:
        data = _gql(session, QUERY, {"input": {"first": PAGE_SIZE, "after": after}})
        conn = data["communityWatchActions"]
        total = conn["totalCount"] if total is None else total
        for edge in conn["edges"] or []:
            n = edge["node"]
            text = n.get("originalContent")
            rows.append({
                "uuid": n["uuid"],
                "comment_id": n["commentId"],
                "source_type": n["sourceType"],
                "reason": n["reason"],
                "action_state": n["actionState"],
                "appeal_state": n["appealState"],
                "review_state": n["reviewState"],
                "content_hash": n.get("contentHash"),
                "content": text,
                "text_available": bool(text) and not n.get("contentCleared", False),
                "report_synced": n.get("reportSynced", False),
                "created_at": n["createdAt"],
                "is_spam": label(n["actionState"], n["reviewState"]),
                # template family for stratified holdout; falls back to uuid if no hash
                "template_family": n.get("contentHash") or n["uuid"],
            })
        page = conn["pageInfo"]
        print(f"  fetched {len(rows)}/{total}", file=sys.stderr)
        if not page["hasNextPage"]:
            break
        after = page["endCursor"]
    return pd.DataFrame(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="community-watch-comment-labels.parquet.gzip")
    args = ap.parse_args()

    df = harvest()
    if df.empty:
        print("No actions harvested.", file=sys.stderr)
        return
    df.to_parquet(args.out, compression="gzip")

    print(f"\nHarvested {len(df)} actions -> {args.out}")
    print(f"  text available: {int(df['text_available'].sum())} / {len(df)}")
    print(f"  label balance:  {df['is_spam'].value_counts().to_dict()}")
    print(f"  by reason:      {df['reason'].value_counts().to_dict()}")
    print(f"  by review_state:{df['review_state'].value_counts().to_dict()}")
    print(f"  template families (distinct content_hash): {df['template_family'].nunique()}")
    # Skew check: report concentration of the largest template family.
    top = df['template_family'].value_counts().head(1)
    if len(top):
        print(f"  largest family: {int(top.iloc[0])} rows "
              f"({100 * top.iloc[0] / len(df):.0f}%)")


if __name__ == "__main__":
    main()
