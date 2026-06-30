"""ring_detect_job 純函式測試（不碰 DB/HTTP；psycopg/requests 在函式內才 import）。
可用 pytest 或 `python scripts/test_ring_detect_job.py`。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ring_detect_job import (  # noqa: E402
    CONTENT_TYPES,
    _load_sql,
    _merge_by_fingerprint,
    assemble_signals,
    build_candidate,
    _severity_of,
)
import ring_signals  # noqa: E402  (eval/ 已被 ring_detect_job 加進 sys.path)


def test_assemble_signals_picks_up_codes_and_brands():
    items = [
        {"content": "币安邀请码 LIDANG 返佣 binance", "author": "zlfeakgv"},
        {"content": "用邀请码 LIDANG 注册 binance.com", "author": "owvrhgip"},
        {"content": "邀请码 LIDANG", "author": "user12345"},
    ]
    sig = assemble_signals(items)
    assert "LIDANG" in sig["sampleCodes"]
    assert "binance" in sig["sampleBrands"]
    # invite:LIDANG 或 brand:binance 跨多個作者 → 實體 ring ≥ 2
    assert sig["entityRingSize"] >= 2
    assert 0.0 <= sig["botUsernameRatio"] <= 1.0


def test_build_candidate_maps_row_and_member_ids():
    row = {
        "template_fam": "abc12345",
        "n_articles": 9,
        "n_authors": 3,
        "new_account_ratio": 0.9,
        "author_ids": [101, 102, 103],
    }
    items = [
        {"content": "28BET 信誉平台 注册送", "author": "aaaa1111"},
        {"content": "28BET 优惠 28bet99.vip", "author": "bbbb2222"},
        {"content": "玩 28BET 就对了", "author": "cccc3333"},
    ]
    c = build_candidate(row, items)
    # fingerprint 改由內容算（正規化），不再是 raw template_fam
    assert c["fingerprint"] != "abc12345" and len(c["fingerprint"]) == 8
    assert c["memberUserIds"] == ["101", "102", "103"]  # 原始 DB id 轉字串
    assert c["nArticles"] == 9 and c["nAuthors"] == 3
    assert c["newAccountRatio"] == 0.9
    assert "28bet" in c["signals"]["sampleBrands"]
    assert c["severity"] in {"low", "medium", "high", "critical"}
    assert isinstance(c["score"], (int, float))


def test_severity_buckets():
    assert _severity_of(0) == "low"
    assert _severity_of(6) == "medium"
    assert _severity_of(15) == "high"
    assert _severity_of(30) == "critical"


def test_new_account_ratio_none_safe():
    row = {"template_fam": "x", "n_articles": 1, "n_authors": 3,
           "new_account_ratio": None, "author_ids": [1]}
    c = build_candidate(row, [{"content": "hi", "author": "u"}])
    assert c["newAccountRatio"] is None


def test_build_candidate_moment_count_maps_to_n_articles():
    """動態 row 用 n_moments；count_col=n_moments → 映到 server 的通用 nArticles。"""
    row = {"template_fam": "m1", "n_moments": 7, "n_authors": 5,
           "new_account_ratio": 1.0, "author_ids": [1, 2, 3, 4, 5]}
    c = build_candidate(row, [{"content": "28BET 注册", "author": "a1"}], count_col="n_moments")
    assert c["nArticles"] == 7  # n_moments 餵進通用貼文數欄
    assert c["nAuthors"] == 5 and c["memberUserIds"] == ["1", "2", "3", "4", "5"]


def test_content_types_registered_and_distinct():
    assert set(CONTENT_TYPES) == {"article", "moment"}
    assert CONTENT_TYPES["moment"]["id_col"] == "moment_ids"
    assert CONTENT_TYPES["moment"]["count_col"] == "n_moments"
    # 兩支內容查詢都回 author_id / author_name / content（detect 只讀這三欄）
    for spec in CONTENT_TYPES.values():
        for col in ("author_id", "author_name", "content"):
            assert col in spec["content_query"]
    assert "FROM moment m" in CONTENT_TYPES["moment"]["content_query"]


def test_load_sql_substitutes_moment_sql():
    """moment 粗篩 SQL 經 _load_sql 後 :var 全換成整數、且帶 moment_ids 給下游精修。"""
    sql = _load_sql(CONTENT_TYPES["moment"]["sql"], days=30, min_authors=3, new_account_days=30)
    assert ":days" not in sql and ":min_authors" not in sql and ":new_account_days" not in sql
    assert "moment_ids" in sql  # 確保有輸出貼文 id 欄，否則 app 層抓不到內容


def test_normalized_fingerprint_collapses_emoji_and_whitespace():
    a = ring_signals.normalized_fingerprint("28BET 注册送 🎰🎰")
    b = ring_signals.normalized_fingerprint("28bet注册送")
    assert a == b  # emoji / 空白 / 大小寫無關
    assert len(a) == 8
    assert ring_signals.normalized_fingerprint("hello") != ring_signals.normalized_fingerprint(
        "buy crypto"
    )  # 真的不同內容 → 不同指紋


def test_normalized_fingerprint_collapses_traditional_simplified():
    try:
        import opencc  # noqa: F401
    except Exception:  # opencc 為選用相依（buildspec 已裝）；本地缺席時略過，CI 會驗
        return
    assert ring_signals.normalized_fingerprint(
        "註冊送優惠"
    ) == ring_signals.normalized_fingerprint("注册送优惠")


def test_merge_by_fingerprint_unions_members_and_signals():
    def cand(fp, ids, sig, n_posts, ratio):
        return {
            "fingerprint": fp,
            "memberUserIds": ids,
            "signals": sig,
            "nArticles": n_posts,
            "nAuthors": len(ids),
            "newAccountRatio": ratio,
            "score": 1,
            "severity": "low",
        }

    a = cand("fp1", ["1", "2"], {"nearDupRingSize": 3, "entityRingSize": 0,
             "topEntity": None, "botUsernameRatio": 0.2, "sampleCodes": ["A"],
             "sampleBrands": []}, 3, 0.5)
    b = cand("fp1", ["2", "3"], {"nearDupRingSize": 1, "entityRingSize": 5,
             "topEntity": "28bet.com", "botUsernameRatio": 0.6, "sampleCodes": ["B"],
             "sampleBrands": ["x"]}, 2, 0.9)
    d = cand("fp2", ["9"], {"nearDupRingSize": 1, "entityRingSize": 0,
             "topEntity": None, "botUsernameRatio": 0.0, "sampleCodes": [],
             "sampleBrands": []}, 1, 0.1)

    out = _merge_by_fingerprint([a, b, d])
    assert len(out) == 2  # fp1 兩筆合一、fp2 不動
    merged = next(c for c in out if c["fingerprint"] == "fp1")
    assert merged["memberUserIds"] == ["1", "2", "3"]  # union + sorted
    assert merged["nAuthors"] == 3 and merged["nArticles"] == 5  # 重算 / 加總
    assert merged["signals"]["nearDupRingSize"] == 3  # max
    assert merged["signals"]["entityRingSize"] == 5  # max
    assert merged["signals"]["topEntity"] == "28bet.com"
    assert set(merged["signals"]["sampleCodes"]) == {"A", "B"}  # 聯集
    assert merged["newAccountRatio"] == 0.9  # max


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn(); print(f"  PASS {fn.__name__}")
        except AssertionError as e:
            failed += 1; print(f"  FAIL {fn.__name__}: {e}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
