#!/usr/bin/env python3
"""軸一 D 正式版 ring 偵測 job（VPC runner JOB=ring 用）。

流程：DB 端粗篩（sql/detect_spam_rings.sql，按正規化模板跨帳號重複）→ 對每個候選 ring
抓成員文章內容 → app 層精修（eval/ring_signals.py 的近似/實體/邀請碼/品牌/亂碼訊號）→
組 candidate payload → 呼叫 matters-server `upsertSpamRingCandidates`（admin）寫成 pending。

**影子先行**：只寫 status=pending 候選，不做任何處置；凍結由管理者在 OSS 控制台手動逐群執行。

環境變數：
  PG_DSN                    read-replica 連線字串（postgresql://...，VPC 內）
  MATTERS_OSS_GQL_ENDPOINT  matters-server GraphQL endpoint（如 https://server.matters.town/graphql）
  MATTERS_OSS_ADMIN_TOKEN   admin service principal token（@auth(mode:admin) 用；header 見 _post_upsert）
  CONTENT_TYPE              article | moment（預設 article）。決定吃哪支粗篩 SQL、抓哪張表的內容做精修。
  DAYS                      近期窗（預設 30）
  MIN_AUTHORS               同模板最少跨帳號數（預設 3）
  NEW_ACCOUNT_DAYS          新帳號門檻天數（預設 30）
  MAX_ARTICLES_PER_RING     每 ring 精修抓內容上限（預設 200，控記憶體）
  DRY_RUN                   非空＝只印候選不寫回 server

文章與動態 ring 共用 spam_ring 表、靠 fingerprint（正規化模板指紋，兩支 SQL 同口徑）跨層去重，
故 upsert 是 idempotent；兩者都走同一個 upsertSpamRingCandidates（server 端 nArticles 為通用貼文數）。
"""
from __future__ import annotations

import collections
import json
import os
import sys
from pathlib import Path

# 與 POC / 控制台同一口徑的訊號函式
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "eval"))
import ring_signals  # noqa: E402

SQL_DIR = Path(__file__).resolve().parent.parent / "sql"

# 內容型別設定：粗篩 SQL、ring 成員貼文 id 欄、貼文數欄、抓內容的查詢（精修用）。
# 兩者 content 查詢都回 author_id / author_name / content，detect() 只讀這三欄。
CONTENT_TYPES = {
    "article": {
        "sql": SQL_DIR / "detect_spam_rings.sql",
        "id_col": "article_ids",
        "count_col": "n_articles",
        "content_query": """
SELECT a.author_id,
       u.user_name AS author_name,
       coalesce(av.title,'') || ' ' || coalesce(ac.content,'') AS content
FROM article a
JOIN article_version_newest av ON av.article_id = a.id
JOIN article_content ac        ON ac.id = av.content_id
JOIN "user" u                  ON u.id = a.author_id
WHERE a.id = ANY(%(ids)s)
""",
    },
    "moment": {
        "sql": SQL_DIR / "detect_spam_rings_moment.sql",
        "id_col": "moment_ids",
        "count_col": "n_moments",
        "content_query": """
SELECT m.author_id,
       u.user_name AS author_name,
       coalesce(m.content, '') AS content
FROM moment m
JOIN "user" u ON u.id = m.author_id
WHERE m.id = ANY(%(ids)s)
""",
    },
}

UPSERT_MUTATION = """
mutation Upsert($input: UpsertSpamRingCandidatesInput!) {
  upsertSpamRingCandidates(input: $input) {
    created updated skipped
  }
}
"""


def _severity_of(score: float) -> str:
    if score >= 25:
        return "critical"
    if score >= 12:
        return "high"
    if score >= 5:
        return "medium"
    return "low"


def assemble_signals(items: list) -> dict:
    """對一個 ring 的 [{content, author}] 算 app 層訊號摘要（純函式，可測）。"""
    texts = [it["content"] for it in items]
    top_ent, ent_ring = ring_signals.top_entity_ring(items)
    near = 0
    for idxs in ring_signals.neardup_groups(texts):
        near = max(near, len({items[i]["author"] for i in idxs}))
    bots = [ring_signals.username_bot_score(it["author"]) for it in items]
    bot_ratio = sum(1 for b in bots if b >= 0.4) / len(bots) if bots else 0.0
    codes, brands = set(), set()
    for it in items:
        for e in ring_signals.advertised_entities(it["content"]):
            if e.startswith("invite:"):
                codes.add(e.split(":", 1)[1])
            elif e.startswith("brand:"):
                brands.add(e.split(":", 1)[1])
    return {
        "nearDupRingSize": near,
        "entityRingSize": ent_ring,
        "topEntity": top_ent,
        "botUsernameRatio": round(bot_ratio, 4),
        "sampleCodes": sorted(codes)[:10],
        "sampleBrands": sorted(brands)[:10],
        "contentModelMax": None,
    }


def build_candidate(row: dict, items: list, count_col: str = "n_articles") -> dict:
    """組一筆 upsert candidate（純函式，可測）。row 來自 detect_spam_rings[_moment].sql。
    count_col：貼文數欄位名（文章＝n_articles、動態＝n_moments）→ 一律映到 server 的 nArticles（通用貼文數）。"""
    signals = assemble_signals(items)
    ring_size = max(signals["nearDupRingSize"], signals["entityRingSize"])
    score = round(ring_size + row["n_authors"] * signals["botUsernameRatio"], 4)
    ratio = row.get("new_account_ratio")
    n_posts = row.get(count_col)
    if n_posts is None:
        n_posts = row.get("n_articles") or row.get("n_moments") or 0
    # 用強化正規化指紋（繁簡/emoji/空白無關）當分群鍵，取 ring 內最常見的那個；
    # detect() 之後再把同指紋的候選合併，避免同內容散成多個 ring。
    fps = [ring_signals.normalized_fingerprint(it.get("content", "")) for it in items]
    fingerprint = (
        collections.Counter(fps).most_common(1)[0][0] if fps else row["template_fam"]
    )
    return {
        "fingerprint": fingerprint,
        "memberUserIds": [str(x) for x in (row.get("author_ids") or [])],
        "signals": signals,
        "nArticles": int(n_posts),
        "nAuthors": int(row["n_authors"]),
        "newAccountRatio": float(ratio) if ratio is not None else None,
        "score": score,
        "severity": _severity_of(score),
    }


def _merge_by_fingerprint(cands: list) -> list:
    """把正規化指紋相同的候選 ring 合併成一筆（純函式，可測）：
    union 成員、重算 nAuthors、加總貼文數、合併訊號（ring size 取 max、codes/brands 取聯集）。"""
    by_fp: dict = {}
    for c in cands:
        by_fp.setdefault(c["fingerprint"], []).append(c)
    out = []
    for fp, group in by_fp.items():
        if len(group) == 1:
            out.append(group[0])
            continue
        member_ids = sorted({m for c in group for m in c["memberUserIds"]})
        sigs = [c["signals"] for c in group]
        merged_sig = {
            "nearDupRingSize": max(s.get("nearDupRingSize", 0) for s in sigs),
            "entityRingSize": max(s.get("entityRingSize", 0) for s in sigs),
            "topEntity": next((s.get("topEntity") for s in sigs if s.get("topEntity")), None),
            "botUsernameRatio": max(s.get("botUsernameRatio", 0.0) for s in sigs),
            "sampleCodes": sorted({x for s in sigs for x in (s.get("sampleCodes") or [])})[:10],
            "sampleBrands": sorted({x for s in sigs for x in (s.get("sampleBrands") or [])})[:10],
            "contentModelMax": None,
        }
        ratios = [c["newAccountRatio"] for c in group if c.get("newAccountRatio") is not None]
        n_posts = sum(int(c.get("nArticles") or 0) for c in group)
        ring_size = max(merged_sig["nearDupRingSize"], merged_sig["entityRingSize"])
        score = round(ring_size + len(member_ids) * merged_sig["botUsernameRatio"], 4)
        out.append({
            "fingerprint": fp,
            "memberUserIds": member_ids,
            "signals": merged_sig,
            "nArticles": n_posts,
            "nAuthors": len(member_ids),
            "newAccountRatio": max(ratios) if ratios else None,
            "score": score,
            "severity": _severity_of(score),
        })
    return out


def _load_sql(sql_path: Path, days: int, min_authors: int, new_account_days: int) -> str:
    """讀粗篩 SQL，把 psql 風格 :var 換成驗證過的整數（給 psycopg 直接執行）。"""
    sql = sql_path.read_text()
    for name, val in (
        ("new_account_days", int(new_account_days)),  # 先換較長的，避免子字串相撞
        ("min_authors", int(min_authors)),
        ("days", int(days)),
    ):
        sql = sql.replace(f":{name}", str(int(val)))
    return sql


def detect(conn, *, content_type: str, days: int, min_authors: int,
           new_account_days: int, max_articles: int) -> list:
    from psycopg.rows import dict_row

    spec = CONTENT_TYPES[content_type]
    rings: list = []
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(_load_sql(spec["sql"], days, min_authors, new_account_days))
        candidates = cur.fetchall()
    for row in candidates:
        post_ids = (row.get(spec["id_col"]) or [])[:max_articles]
        if not post_ids:
            continue
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(spec["content_query"], {"ids": list(post_ids)})
            posts = cur.fetchall()
        items = [
            {"content": p["content"] or "", "author": p["author_name"] or str(p["author_id"])}
            for p in posts
        ]
        if not items:
            continue
        cand = build_candidate(row, items, count_col=spec["count_col"])
        if cand["fingerprint"] == ring_signals.EMPTY_FINGERPRINT:
            # 內容正規化後為空（純圖/emoji/url/空白貼文）→ 無文字模板可比對，
            # 不成 ring（否則會把不相干的帳號全擠成一個假群，如 d41d8cd9）。
            continue
        rings.append(cand)
    return _merge_by_fingerprint(rings)


def _post_upsert(endpoint: str, token: str, candidates: list) -> dict:
    import requests

    resp = requests.post(
        endpoint,
        json={"query": UPSERT_MUTATION, "variables": {"input": {"candidates": candidates}}},
        headers={
            "Content-Type": "application/json",
            # matters-server admin 認證：實際 header/token 取得方式為 infra 設定項，
            # 預設用 Authorization Bearer；若採 x-access-token 改這裡。
            "Authorization": f"Bearer {token}",
            "x-access-token": token,
        },
        timeout=120,
    )
    body = resp.json()
    if body.get("errors"):
        raise RuntimeError(f"upsert failed: {str(body['errors'])[:300]}")
    return body["data"]["upsertSpamRingCandidates"]


def main() -> int:
    content_type = os.environ.get("CONTENT_TYPE", "article")
    if content_type not in CONTENT_TYPES:
        print(f"CONTENT_TYPE must be one of {sorted(CONTENT_TYPES)}; got {content_type!r}",
              file=sys.stderr)
        return 2
    days = int(os.environ.get("DAYS", "30"))
    min_authors = int(os.environ.get("MIN_AUTHORS", "3"))
    new_account_days = int(os.environ.get("NEW_ACCOUNT_DAYS", "30"))
    max_articles = int(os.environ.get("MAX_ARTICLES_PER_RING", "200"))
    dry_run = bool(os.environ.get("DRY_RUN"))
    dsn = os.environ.get("PG_DSN")
    if not dsn:
        print("PG_DSN required", file=sys.stderr)
        return 2

    import psycopg

    with psycopg.connect(dsn) as conn:
        candidates = detect(
            conn,
            content_type=content_type,
            days=days,
            min_authors=min_authors,
            new_account_days=new_account_days,
            max_articles=max_articles,
        )
    print(f"detected {len(candidates)} {content_type} ring candidate(s)")

    if dry_run:
        print(json.dumps(candidates, ensure_ascii=False, indent=2)[:4000])
        return 0
    if not candidates:
        return 0

    endpoint = os.environ.get("MATTERS_OSS_GQL_ENDPOINT")
    token = os.environ.get("MATTERS_OSS_ADMIN_TOKEN")
    if not endpoint or not token:
        # 影子先行：endpoint/token 未設時不寫回，只印候選（不讓 buildspec 失敗）
        print("MATTERS_OSS_GQL_ENDPOINT / MATTERS_OSS_ADMIN_TOKEN not set — shadow only, skip upsert",
              file=sys.stderr)
        print(json.dumps(candidates, ensure_ascii=False)[:2000])
        return 0
    result = _post_upsert(endpoint, token, candidates)
    print(f"upserted: {json.dumps(result)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
