"""Harvest NORMAL (non-spam) comments as negative samples for the comment model.

Why
---
The Community Watch board (`harvest_community_watch.py`) gives us almost only
POSITIVES (spam): 528 spam / ~2 cleared in the live data. A classifier trained
on that alone learns "everything is spam" and over-kill (false-positive rate)
cannot be measured. We need a realistic NEGATIVE set: ordinary article comments
that were NOT removed by Community Watch.

Source (all PUBLIC, no token)
------------------------------
  search(type: Article)            -> article ids               (public)
  Article.comments(input)          -> comment {id, content}      (public)
`Comment.contentHash` is admin-only, so we compute our own normalized hash for
the template-family key (used for grouped holdout, consistent with the positive
harvester's contentHash role -- families are per-corpus, not cross-joined).

Cleanliness
-----------
We exclude any comment whose id appears in the Community Watch removal set
(pass --exclude-labels pointing at the positives parquet). Remaining comments on
ordinary articles are treated as negatives (is_spam=0). This is provisional
ground truth: a few undetected spam may leak in, which is acceptable noise for a
negative set and is the conservative direction (it can only depress, not inflate,
measured recall).

Usage
-----
    export MATTERS_API=https://server.matters.town/graphql
    python harvest_normal_comments.py \
        --keywords-file keywords.txt \
        --exclude-labels community-watch-comment-labels.parquet.gzip \
        --target 1200 --out normal-comments.parquet.gzip

Output columns: comment_id, content, content_text, content_hash, article_id,
author, created_at, is_spam(=0), template_family
"""
import argparse
import hashlib
import os
import re
import sys
import time

import pandas as pd
import requests

MATTERS_API = os.environ.get("MATTERS_API", "https://server.matters.town/graphql")

# A spread of generic, topic-diverse keywords so negatives are not skewed to one
# domain. Chinese + a couple latin terms; Matters is zh-dominant.
DEFAULT_KEYWORDS = [
    "台灣", "香港", "中國", "民主", "經濟", "科技", "電影", "音樂", "旅行",
    "讀書", "生活", "健康", "投資", "教育", "歷史", "藝術", "美食", "攝影",
    "心理", "環境", "女性", "工作", "創作", "遊戲", "新聞",
]

SEARCH_Q = """
query Neg($i: SearchInput!) {
  search(input: $i) {
    pageInfo { endCursor hasNextPage }
    edges { node { ... on Article {
      id
      comments(input: { first: 30, sort: newest }) {
        edges { node { id content author { userName } createdAt } }
      }
    } } }
  }
}
"""

_tag_re = re.compile(r"<[^>]+>")
_ws_re = re.compile(r"\s+")


def normalize(text):
    """Strip HTML tags and collapse whitespace -> plain text for hashing/training."""
    if not text:
        return ""
    t = _tag_re.sub(" ", text)
    t = (t.replace("&nbsp;", " ").replace("&amp;", "&")
           .replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"'))
    return _ws_re.sub(" ", t).strip()


def fam_hash(text):
    return hashlib.sha256(normalize(text).encode("utf-8")).hexdigest()


def _gql(session, query, variables):
    headers = {"Content-Type": "application/json", "x-client-name": "spam-neg-harvest"}
    last = None
    for delay in [0, 3, 10, 20]:
        if delay:
            time.sleep(delay)
        try:
            resp = session.post(MATTERS_API, json={"query": query, "variables": variables},
                                headers=headers, timeout=60)
        except requests.exceptions.RequestException as e:
            last = e
            continue
        if 500 <= resp.status_code < 600:
            last = RuntimeError(f"HTTP {resp.status_code}")
            continue
        body = resp.json()
        if body.get("errors"):
            raise RuntimeError(f"GraphQL error: {body['errors']}")
        return body["data"]
    raise RuntimeError(f"failed after retries: {last}")


def harvest(keywords, target, exclude_ids, pages_per_kw):
    session = requests.Session()
    rows, seen = [], set()
    for kw in keywords:
        after = None
        for _ in range(pages_per_kw):
            data = _gql(session, SEARCH_Q,
                        {"i": {"key": kw, "type": "Article", "first": 20,
                               "after": after, "record": False}})
            search = data["search"]
            for edge in search["edges"] or []:
                node = edge.get("node") or {}
                comments = (node.get("comments") or {}).get("edges") or []
                for ce in comments:
                    c = ce["node"]
                    cid = c["id"]
                    if cid in seen or cid in exclude_ids:
                        continue
                    text = normalize(c.get("content"))
                    if len(text) < 2:  # drop empties
                        continue
                    seen.add(cid)
                    rows.append({
                        "comment_id": cid,
                        "content": c.get("content"),
                        "content_text": text,
                        "content_hash": fam_hash(c.get("content")),
                        "article_id": node.get("id"),
                        "author": (c.get("author") or {}).get("userName"),
                        "created_at": c.get("createdAt"),
                        "is_spam": 0,
                        "template_family": fam_hash(c.get("content")),
                    })
            page = search["pageInfo"]
            print(f"  kw={kw!r}: total negatives so far {len(rows)}", file=sys.stderr)
            if len(rows) >= target:
                return pd.DataFrame(rows)
            if not page["hasNextPage"]:
                break
            after = page["endCursor"]
    return pd.DataFrame(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--keywords-file")
    ap.add_argument("--exclude-labels", default="community-watch-comment-labels.parquet.gzip")
    ap.add_argument("--target", type=int, default=1200)
    ap.add_argument("--pages-per-kw", type=int, default=3)
    ap.add_argument("--out", default="normal-comments.parquet.gzip")
    args = ap.parse_args()

    keywords = DEFAULT_KEYWORDS
    if args.keywords_file and os.path.exists(args.keywords_file):
        with open(args.keywords_file) as fh:
            keywords = [ln.strip() for ln in fh if ln.strip()]

    exclude_ids = set()
    if os.path.exists(args.exclude_labels):
        pos = pd.read_parquet(args.exclude_labels)
        if "comment_id" in pos.columns:
            exclude_ids = set(pos["comment_id"].dropna().astype(str))
        print(f"Excluding {len(exclude_ids)} Community-Watch comment ids.", file=sys.stderr)

    df = harvest(keywords, args.target, exclude_ids, args.pages_per_kw)
    if df.empty:
        print("No negatives harvested.", file=sys.stderr)
        return
    df = df.drop_duplicates(subset=["comment_id"]).reset_index(drop=True)
    df.to_parquet(args.out, compression="gzip")

    print(f"\nHarvested {len(df)} normal comments -> {args.out}")
    print(f"  distinct authors:  {df['author'].nunique()}")
    print(f"  distinct families: {df['template_family'].nunique()}")
    print(f"  median text len:   {int(df['content_text'].str.len().median())}")


if __name__ == "__main__":
    main()
