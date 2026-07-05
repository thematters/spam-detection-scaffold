#!/usr/bin/env python3
"""軸一 D 正式版 ring 偵測 job（VPC runner JOB=ring 用）。

流程：DB 端粗篩（sql/detect_spam_rings.sql，按正規化模板跨帳號重複）→ 對每個候選 ring
抓成員文章內容 → app 層精修（eval/ring_signals.py 的近似/實體/邀請碼/品牌/亂碼訊號）→
組 candidate payload → 呼叫 matters-server `upsertSpamRingCandidates`（admin）寫成 pending。

預設**影子先行**：只寫 status=pending 候選，凍結由管理者在 OSS 控制台手動逐群執行。
v2（SPEC_RING_V2）新增 AUTO_FREEZE 閘門：開啟時對「雙鑰成立且非老帳號豁免」的 pending ring
呼叫 freezeSpamRing（server 端會逐成員略過老帳號/高 karma，為第二道護欄）。

環境變數：
  PG_DSN                    read-replica 連線字串（postgresql://...，VPC 內）
  MATTERS_OSS_GQL_ENDPOINT  matters-server GraphQL endpoint（如 https://server.matters.town/graphql）
  MATTERS_OSS_ADMIN_TOKEN   admin service principal token（@auth(mode:admin) 用；header 見 _post_upsert）
  CONTENT_TYPE              article | moment（預設 article）。決定吃哪支粗篩 SQL、抓哪張表的內容做精修。
  DAYS                      近期窗（預設 30）
  MIN_AUTHORS               同模板最少跨帳號數（預設 3）
  SINGLE_AUTHOR_MIN_POSTS   F1a 單帳號洗文門檻：同帳號同模板 ≥N 篇成候選（預設 3；永不自動凍結）
  NEW_ACCOUNT_DAYS          新帳號門檻天數（預設 30）
  MAX_ARTICLES_PER_RING     每 ring 精修抓內容上限（預設 200，控記憶體）
  DRY_RUN                   非空＝只印候選不寫回 server
  AUTO_FREEZE               非空＝對合格 ring 自動凍結（F3b；預設關＝Dark。開啟前提：server 已
                            部署 upsert 回傳 rings、security review 通過——見 SPEC_RING_V2 §10）
  AUTO_FREEZE_HIGH_AUTHORS  自動凍結鑰1：跨帳號數門檻（預設 3，F1b 由 5 降 3）
  AUTO_FREEZE_NEW_RATIO_HI  鑰2a：新帳號比例門檻（預設 0.8）
  AUTO_FREEZE_BOT_RATIO_HI  鑰2b：亂碼帳號名比例門檻（預設 0.5）
  AUTO_FREEZE_OLD_EXEMPT    老帳號豁免：新帳號比低於此且亂碼低 → 永不自動凍結（預設 0.34）

文章與動態 ring 共用 spam_ring 表、靠 fingerprint（正規化模板指紋，兩支 SQL 同口徑）跨層去重，
故 upsert 是 idempotent；兩者都走同一個 upsertSpamRingCandidates（server 端 nArticles 為通用貼文數）。
"""
from __future__ import annotations

import collections
import json
import os
import re
import sys
from pathlib import Path

# 與 POC / 控制台同一口徑的訊號函式
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "eval"))
import ring_signals  # noqa: E402

SQL_DIR = Path(__file__).resolve().parent.parent / "sql"
TAG_RE = re.compile(r"<[^>]+>")
URL_RE = re.compile(r"https?://\S+")
WS_RE = re.compile(r"\s+")

# 內容型別設定：粗篩 SQL、ring 成員貼文 id 欄、貼文數欄、抓內容的查詢（精修用）。
# 兩者 content 查詢都回 author_id / author_name / content / is_new_account，
# detect() 只讀這四欄（is_new_account 給成員過濾後重算 new_account_ratio 用）。
CONTENT_TYPES = {
    "article": {
        "sql": SQL_DIR / "detect_spam_rings.sql",
        "id_col": "article_ids",
        "count_col": "n_articles",
        "content_query": """
SELECT a.author_id,
       u.user_name AS author_name,
       coalesce(av.title,'') || ' ' || coalesce(ac.content,'') AS content,
       (u.created_at >= now() - (%(new_account_days)s || ' days')::interval) AS is_new_account
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
       coalesce(m.content, '') AS content,
       (u.created_at >= now() - (%(new_account_days)s || ' days')::interval) AS is_new_account
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

# AUTO_FREEZE 用：多要 rings（global id / 指紋 / 現況），才能對合格者呼叫 freezeSpamRing。
# 舊版 server 沒有 rings 欄位會直接報 GraphQL validation error——所以只在 AUTO_FREEZE 開啟時用
# 這支（部署順序見 SPEC_RING_V2 §10：server 先上、AUTO_FREEZE 後開）。
UPSERT_MUTATION_WITH_RINGS = """
mutation Upsert($input: UpsertSpamRingCandidatesInput!) {
  upsertSpamRingCandidates(input: $input) {
    created updated skipped
    rings { id fingerprint status }
  }
}
"""

FREEZE_MUTATION = """
mutation Freeze($input: FreezeSpamRingInput!) {
  freezeSpamRing(input: $input) {
    ring { id status }
    frozen { id }
    skipped { reason }
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


def _sample_texts(items: list, *, limit: int = 5, chars: int = 36) -> list[str]:
    samples: list[str] = []
    seen = set()
    for item in items:
        text = item.get("content") or ""
        text = TAG_RE.sub(" ", text)
        text = URL_RE.sub(" ", text)
        text = WS_RE.sub(" ", text).strip()
        if not text:
            continue
        sample = text[:chars]
        key = sample.casefold()
        if key in seen:
            continue
        seen.add(key)
        samples.append(sample)
        if len(samples) >= limit:
            break
    return samples


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
        "sampleTexts": _sample_texts(items),
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
        # 加權平均而非 max（審查 F2）：取 max 會讓「老帳號群＋新帳號群」合併後
        # 整包看起來全新、拖進雙鑰自動凍結；權重＝各群跨帳號數。
        weighted = [(c["newAccountRatio"], int(c.get("nAuthors") or 0))
                    for c in group if c.get("newAccountRatio") is not None]
        w_total = sum(w for _, w in weighted)
        ratio = (sum(r * w for r, w in weighted) / w_total) if w_total else None
        n_posts = sum(int(c.get("nArticles") or 0) for c in group)
        ring_size = max(merged_sig["nearDupRingSize"], merged_sig["entityRingSize"])
        score = round(ring_size + len(member_ids) * merged_sig["botUsernameRatio"], 4)
        merged = {
            "fingerprint": fp,
            "memberUserIds": member_ids,
            "signals": merged_sig,
            "nArticles": n_posts,
            "nAuthors": len(member_ids),
            "newAccountRatio": round(ratio, 4) if ratio is not None else None,
            "score": score,
            "severity": _severity_of(score),
        }
        # 內部欄位（"_" 前綴，upsert 前剝除）：驗證成員取聯集、截斷取 OR
        verified = sorted({m for c in group for m in (c.get("_verifiedMemberIds") or [])})
        if verified:
            merged["_verifiedMemberIds"] = verified
        if any(c.get("_truncated") for c in group):
            merged["_truncated"] = True
        out.append(merged)
    return out


def strip_internal_keys(cands: list) -> list:
    """upsert payload 只留 GraphQL input 欄位；"_" 前綴為 job 內部決策用。"""
    return [{k: v for k, v in c.items() if not k.startswith("_")} for c in cands]


def filter_empty_members(row: dict, items: list, count_col: str) -> tuple[dict, list]:
    """F3a（SPEC_RING_V2）成員級純圖過濾（純函式，可測）。

    SQL 粗篩會把「分享圖片/純 emoji/純連結、無文字內文」的貼文湊進 ring；整 ring 的空指紋
    跳過（EMPTY_FINGERPRINT）擋不住**混合 ring**——真人老帳號因一則純圖動態被掛進成員名單，
    是誤列主因、也是自動凍結的前置阻礙。這裡逐成員檢查：該成員在本 ring 內的貼文若「全部」
    正規化後為空，就從成員與訊號中剔除，並以剩餘成員重算 n_authors / 貼文數 / new_account_ratio。

    注意：只對「抓得到內容」的成員做判斷（items 受 MAX_ARTICLES_PER_RING 上限影響）；
    看不到貼文的成員一律保留，寧可送人工也不誤刪證據。
    """
    by_author: dict = collections.defaultdict(list)
    for it in items:
        by_author[str(it.get("author_id"))].append(it)
    empty_ids = {
        a
        for a, its in by_author.items()
        if a != "None"
        and all(
            ring_signals.normalized_fingerprint(it.get("content", ""))
            == ring_signals.EMPTY_FINGERPRINT
            for it in its
        )
    }
    if not empty_ids:
        return row, items

    kept_items = [it for it in items if str(it.get("author_id")) not in empty_ids]
    kept_ids = [x for x in (row.get("author_ids") or []) if str(x) not in empty_ids]
    dropped_posts = len(items) - len(kept_items)

    row2 = dict(row)
    row2["author_ids"] = kept_ids
    row2["n_authors"] = max(0, int(row.get("n_authors") or 0) - len(empty_ids))
    if row.get(count_col) is not None:
        row2[count_col] = max(0, int(row[count_col]) - dropped_posts)
    # 以「還看得到」的剩餘成員重算 new_account_ratio（每帳號一票，不是每貼文一票）
    flags = {
        str(it["author_id"]): bool(it["is_new_account"])
        for it in kept_items
        if it.get("is_new_account") is not None
    }
    if flags:
        row2["new_account_ratio"] = sum(flags.values()) / len(flags)
    return row2, kept_items


def auto_freeze_eligible(cand: dict, *, high_authors: int = 3, new_ratio_hi: float = 0.8,
                         bot_ratio_hi: float = 0.5, old_exempt_ratio: float = 0.34) -> bool:
    """F3b（SPEC_RING_V2）自動凍結決策（純函式，可測）——與影子週驗證的 ring_decide 同構：

      雙鑰：跨帳號 ≥ high_authors（F1b 預設 3）＋（新帳號比 ≥ new_ratio_hi 或 亂碼比 ≥ bot_ratio_hi）
      硬性豁免：新帳號比 < old_exempt_ratio 且亂碼低 → 永不自動凍結（送人工）
      資料缺席即否決：newAccountRatio 為 None → False（沒證據就不動手）
      單帳號候選（F1a，nAuthors=1）天然不合格。

    Q1 教訓（PR #4887）：不設任何「高信心繞過豁免」；server 端 freezeSpamRing 另會
    逐成員略過老帳號/高 karma，為第二道網。
    """
    n_authors = int(cand.get("nAuthors") or 0)
    if n_authors < high_authors:
        return False
    ratio = cand.get("newAccountRatio")
    if ratio is None:
        return False
    bot = float((cand.get("signals") or {}).get("botUsernameRatio") or 0.0)
    if ratio < old_exempt_ratio and bot < bot_ratio_hi:
        return False
    return ratio >= new_ratio_hi or bot >= bot_ratio_hi


def _load_sql(sql_path: Path, days: int, min_authors: int, new_account_days: int,
              single_author_min_posts: int = 3) -> str:
    """讀粗篩 SQL，把 psql 風格 :var 換成驗證過的整數（給 psycopg 直接執行）。"""
    sql = sql_path.read_text()
    for name, val in (
        ("single_author_min_posts", int(single_author_min_posts)),  # 先換較長的，避免子字串相撞
        ("new_account_days", int(new_account_days)),
        ("min_authors", int(min_authors)),
        ("days", int(days)),
    ):
        sql = sql.replace(f":{name}", str(int(val)))
    return sql


def detect(conn, *, content_type: str, days: int, min_authors: int,
           new_account_days: int, max_articles: int,
           single_author_min_posts: int = 3) -> list:
    from psycopg.rows import dict_row

    spec = CONTENT_TYPES[content_type]
    rings: list = []
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(_load_sql(spec["sql"], days, min_authors, new_account_days,
                              single_author_min_posts))
        candidates = cur.fetchall()
    for row in candidates:
        post_ids = (row.get(spec["id_col"]) or [])[:max_articles]
        if not post_ids:
            continue
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(spec["content_query"],
                        {"ids": list(post_ids), "new_account_days": str(int(new_account_days))})
            posts = cur.fetchall()
        items = [
            {"content": p["content"] or "",
             "author": p["author_name"] or str(p["author_id"]),
             "author_id": p["author_id"],
             "is_new_account": p.get("is_new_account")}
            for p in posts
        ]
        if not items:
            continue
        # F3a：先做成員級純圖過濾，再看門檻（否則純圖成員撐出來的 n_authors 是虛的）
        row, items = filter_empty_members(row, items, spec["count_col"])
        n_authors = int(row.get("n_authors") or 0)
        n_posts = int(row.get(spec["count_col"]) or 0)
        if n_authors <= 0 or not items:
            continue
        if n_authors < min_authors and not (
            n_authors == 1 and n_posts >= single_author_min_posts
        ):
            # 過濾後掉出 ring 門檻、又不構成單帳號洗文（F1a）→ 丟棄，等它長大再說
            continue
        cand = build_candidate(row, items, count_col=spec["count_col"])
        if cand["fingerprint"] == ring_signals.EMPTY_FINGERPRINT:
            # 內容正規化後為空（純圖/emoji/url/空白貼文）→ 無文字模板可比對，
            # 不成 ring（否則會把不相干的帳號全擠成一個假群，如 d41d8cd9）。
            continue
        # 審查 F1：自動凍結只能碰「本輪實際抓到貼文、通過純圖過濾」的成員；
        # 截斷 ring（貼文數超過抓取上限）含未驗證成員 → 降級人工。
        cand["_verifiedMemberIds"] = sorted({str(it["author_id"]) for it in items})
        if len(row.get(spec["id_col"]) or []) > max_articles:
            cand["_truncated"] = True
        rings.append(cand)
    return _merge_by_fingerprint(rings)


def _gql_headers(token: str) -> dict:
    return {
        "Content-Type": "application/json",
        # matters-server admin 認證：實際 header/token 取得方式為 infra 設定項，
        # 預設用 Authorization Bearer；若採 x-access-token 改這裡。
        "Authorization": f"Bearer {token}",
        "x-access-token": token,
    }


def _post_upsert(endpoint: str, token: str, candidates: list, *,
                 with_rings: bool = False) -> dict:
    import requests

    mutation = UPSERT_MUTATION_WITH_RINGS if with_rings else UPSERT_MUTATION
    resp = requests.post(
        endpoint,
        json={"query": mutation, "variables": {"input": {"candidates": candidates}}},
        headers=_gql_headers(token),
        timeout=120,
    )
    body = resp.json()
    if body.get("errors"):
        raise RuntimeError(f"upsert failed: {str(body['errors'])[:300]}")
    return body["data"]["upsertSpamRingCandidates"]


def _post_freeze(endpoint: str, token: str, ring_id: str, remark: str,
                 member_user_ids: list | None = None) -> dict:
    import requests

    freeze_input: dict = {"id": ring_id, "remark": remark}
    if member_user_ids:
        # 審查 F1：交集凍結——server 端成員表是歷次 upsert 聯集（只增不減），
        # 自動化只准碰本輪驗證過的成員；名單外（歷史誤列/未驗證）留給人工。
        freeze_input["memberUserIds"] = member_user_ids
    resp = requests.post(
        endpoint,
        json={"query": FREEZE_MUTATION,
              "variables": {"input": freeze_input}},
        headers=_gql_headers(token),
        timeout=120,
    )
    body = resp.json()
    if body.get("errors"):
        raise RuntimeError(f"freeze failed for {ring_id}: {str(body['errors'])[:300]}")
    return body["data"]["freezeSpamRing"]


def auto_freeze(endpoint: str, token: str, candidates: list, rings: list, cfg: dict) -> dict:
    """對「決策合格 × server 回報仍 pending」的 ring 執行凍結。

    只碰 status=pending：dismissed（人工判過誤判）與 restored（人工解凍過）是明確的
    人類否決訊號，自動化永不推翻。逐 ring try/except——單一失敗不擋其他 ring，
    結果彙總給日報/稽核。
    """
    by_fp = {r["fingerprint"]: r for r in rings if r.get("fingerprint")}
    summary = {"frozen": [], "skipped_status": [], "skipped_unverified": [],
               "ineligible": 0, "errors": []}
    for cand in candidates:
        if not auto_freeze_eligible(cand, **cfg):
            summary["ineligible"] += 1
            continue
        # 審查 F1：截斷 ring 含未驗證成員 → 降級人工；驗證成員數自身也要過鑰1
        # 門檻（否則「大 ring 但只驗證到 2 人」會以小樣本凍結）。
        verified = cand.get("_verifiedMemberIds") or []
        if cand.get("_truncated") or len(verified) < cfg.get("high_authors", 3):
            summary["skipped_unverified"].append({
                "fingerprint": cand["fingerprint"],
                "truncated": bool(cand.get("_truncated")),
                "n_verified": len(verified),
            })
            continue
        ring = by_fp.get(cand["fingerprint"])
        if not ring:
            continue
        if ring.get("status") != "pending":
            summary["skipped_status"].append(
                {"fingerprint": cand["fingerprint"], "status": ring.get("status")})
            continue
        remark = (f"auto-freeze v2 雙鑰：跨{cand['nAuthors']}帳號 "
                  f"新{(cand.get('newAccountRatio') or 0):.0%} "
                  f"亂碼{(cand['signals'].get('botUsernameRatio') or 0):.0%}"
                  f"；驗證成員 {len(verified)}")
        try:
            result = _post_freeze(endpoint, token, ring["id"], remark,
                                  member_user_ids=verified)
            summary["frozen"].append({
                "fingerprint": cand["fingerprint"],
                "ring_id": ring["id"],
                "frozen": len(result.get("frozen") or []),
                "skipped_members": len(result.get("skipped") or []),
            })
        except Exception as exc:  # noqa: BLE001 — 單 ring 失敗不擋整批
            summary["errors"].append({"fingerprint": cand["fingerprint"], "error": str(exc)[:200]})
    return summary


def main() -> int:
    content_type = os.environ.get("CONTENT_TYPE", "article")
    if content_type not in CONTENT_TYPES:
        print(f"CONTENT_TYPE must be one of {sorted(CONTENT_TYPES)}; got {content_type!r}",
              file=sys.stderr)
        return 2
    days = int(os.environ.get("DAYS", "30"))
    min_authors = int(os.environ.get("MIN_AUTHORS", "3"))
    single_author_min_posts = int(os.environ.get("SINGLE_AUTHOR_MIN_POSTS", "3"))
    new_account_days = int(os.environ.get("NEW_ACCOUNT_DAYS", "30"))
    max_articles = int(os.environ.get("MAX_ARTICLES_PER_RING", "200"))
    dry_run = bool(os.environ.get("DRY_RUN"))
    auto_freeze_on = bool(os.environ.get("AUTO_FREEZE"))
    freeze_cfg = {
        "high_authors": int(os.environ.get("AUTO_FREEZE_HIGH_AUTHORS", "3")),
        "new_ratio_hi": float(os.environ.get("AUTO_FREEZE_NEW_RATIO_HI", "0.8")),
        "bot_ratio_hi": float(os.environ.get("AUTO_FREEZE_BOT_RATIO_HI", "0.5")),
        "old_exempt_ratio": float(os.environ.get("AUTO_FREEZE_OLD_EXEMPT", "0.34")),
    }
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
            single_author_min_posts=single_author_min_posts,
        )
    print(f"detected {len(candidates)} {content_type} ring candidate(s)")

    if dry_run:
        for c in candidates:
            c["autoFreezeEligibleDryRun"] = auto_freeze_eligible(c, **freeze_cfg)
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
    result = _post_upsert(endpoint, token, strip_internal_keys(candidates),
                          with_rings=auto_freeze_on)
    print(f"upserted: {json.dumps({k: v for k, v in result.items() if k != 'rings'})}")

    if auto_freeze_on:
        summary = auto_freeze(endpoint, token, candidates, result.get("rings") or [], freeze_cfg)
        print(f"auto-freeze: {json.dumps(summary, ensure_ascii=False)}")
        if summary["errors"]:
            # 凍結有失敗時讓 build 標紅，逼人來看；upsert 本身已成功不回滾
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
