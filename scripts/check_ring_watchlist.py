#!/usr/bin/env python3
"""Check known spam-ring watchlist families through public Matters search.

This is read-only and does not upsert candidates or freeze accounts. Use it when
the operator-facing ring digest looks stale, to separate detector signal health
from CodeBuild/EventBridge scheduling problems.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "eval"))

from ring_signals import (  # noqa: E402
    neardup_groups,
    template_family,
    top_entity_ring,
    username_bot_score,
)
from ring_watchlist import WATCHLIST  # noqa: E402

DEFAULT_ENDPOINT = "https://server.matters.town/graphql"

SEARCH_QUERY = """
query RingWatchlistSearch($input: SearchInput!) {
  search(input: $input) {
    totalCount
    edges {
      node {
        id
        ... on Article {
          title
          content
          author { userName }
          shortHash
          createdAt
        }
      }
    }
  }
}
"""


def gql(endpoint: str, query: str, variables: dict, *, timeout: int) -> dict:
    payload = json.dumps({"query": query, "variables": variables}).encode()
    req = urllib.request.Request(
        endpoint,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "spam-ring-watchlist-readonly",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = json.load(resp)
    if body.get("errors"):
        raise RuntimeError(json.dumps(body["errors"][:2], ensure_ascii=False))
    return body["data"]


def analyze_case(endpoint: str, case: dict, *, first: int, timeout: int) -> dict:
    data = gql(
        endpoint,
        SEARCH_QUERY,
        {"input": {"type": "Article", "key": case["query"], "first": first, "record": False}},
        timeout=timeout,
    )["search"]
    items = []
    examples = []
    for edge in data.get("edges") or []:
        node = edge.get("node") or {}
        if not node.get("id"):
            continue
        author = (node.get("author") or {}).get("userName") or "?"
        title = node.get("title") or ""
        content = f"{title} {node.get('content') or ''}"
        items.append({"content": content, "author": author})
        if len(examples) < 3:
            examples.append({
                "title": title,
                "author": author,
                "shortHash": node.get("shortHash"),
                "createdAt": node.get("createdAt"),
            })

    texts = [item["content"] for item in items]
    authors = {item["author"] for item in items}
    exact_families = {template_family(item["content"]) for item in items}
    top_near = 0
    for idxs in neardup_groups(texts):
        top_near = max(top_near, len({items[i]["author"] for i in idxs}))
    bot_scores = [username_bot_score(item["author"]) for item in items]
    bot_ratio = (
        sum(1 for score in bot_scores if score >= 0.4) / len(bot_scores)
        if bot_scores
        else 0.0
    )
    top_entity, entity_ring = top_entity_ring(items)
    return {
        **case,
        "totalCount": data.get("totalCount") or 0,
        "sampleCount": len(items),
        "authors": len(authors),
        "exactFamilies": len(exact_families),
        "topNearRingAccounts": top_near,
        "topEntityRingAccounts": entity_ring,
        "topEntity": top_entity,
        "botUsernameRatio": round(bot_ratio, 4),
        "examples": examples,
    }


def print_table(rows: list[dict]) -> None:
    print(
        "id\ttotal\tsample\tauthors\texact\tnear\tentity\tbot%\ttop_entity"
    )
    for row in rows:
        print(
            f"{row['id']}\t{row['totalCount']}\t{row['sampleCount']}\t"
            f"{row['authors']}\t{row['exactFamilies']}\t"
            f"{row['topNearRingAccounts']}\t{row['topEntityRingAccounts']}\t"
            f"{row['botUsernameRatio'] * 100:.0f}\t{row.get('topEntity') or '-'}"
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--endpoint", default=os.getenv("MATTERS_PUBLIC_GQL_ENDPOINT", DEFAULT_ENDPOINT))
    parser.add_argument("--first", type=int, default=int(os.getenv("RING_WATCHLIST_FIRST", "30")))
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--json", action="store_true", help="print JSON instead of a TSV table")
    parser.add_argument("--sleep", type=float, default=0.2, help="seconds between cases")
    args = parser.parse_args()

    rows = []
    for case in WATCHLIST:
        rows.append(analyze_case(args.endpoint, case, first=args.first, timeout=args.timeout))
        time.sleep(args.sleep)
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    else:
        print_table(rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

