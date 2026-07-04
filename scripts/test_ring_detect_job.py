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
    auto_freeze,
    auto_freeze_eligible,
    build_candidate,
    filter_empty_members,
    _severity_of,
)
import ring_detect_job  # noqa: E402  (auto_freeze 測試要 monkeypatch _post_freeze)
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


def test_assemble_signals_includes_readable_sample_texts():
    items = [
        {"content": "<p>老燈律師翻譯社 邀請碼 LIDANG</p> https://example.com/a", "author": "a"},
        {"content": "<p>老燈律師翻譯社 邀請碼 LIDANG</p> https://example.com/a", "author": "b"},
        {"content": "加拿大翻譯社 免費諮詢 line", "author": "c"},
    ]
    sig = assemble_signals(items)
    assert sig["sampleTexts"] == [
        "老燈律師翻譯社 邀請碼 LIDANG",
        "加拿大翻譯社 免費諮詢 line",
    ]
    assert all("http" not in sample and "<" not in sample for sample in sig["sampleTexts"])


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


def test_empty_content_maps_to_empty_fingerprint_and_is_skippable():
    # md5("") 前 8 碼；純圖/emoji/url/空白/HTML-only 都正規化成空 → 同一個空指紋
    assert ring_signals.EMPTY_FINGERPRINT == "d41d8cd9"
    for txt in ["", "   \n  ", "🎰🎰", "https://x.com/a", "<p></p>", "@user"]:
        assert ring_signals.normalized_fingerprint(txt) == ring_signals.EMPTY_FINGERPRINT
    # build_candidate 對純空白/emoji 內容 → 空指紋（detect 會據此跳過，不成假 ring）
    row = {"template_fam": "x", "n_articles": 5, "n_authors": 5,
           "new_account_ratio": 1.0, "author_ids": [1, 2, 3, 4, 5]}
    c = build_candidate(row, [{"content": "🎰", "author": "a"},
                              {"content": "   ", "author": "b"}])
    assert c["fingerprint"] == ring_signals.EMPTY_FINGERPRINT


def test_filter_empty_members_drops_image_only_member():
    """F3a：混合 ring 裡「貼文全為純圖/無內文」的成員被剔除，計數與新帳號比以剩餘成員重算。"""
    row = {"template_fam": "x", "n_moments": 5, "n_authors": 3,
           "new_account_ratio": 0.67, "author_ids": [1, 2, 3]}
    items = [
        # 成員 1、2：真 spam 模板（新帳號）
        {"content": "28BET 注册送", "author": "a1", "author_id": 1, "is_new_account": True},
        {"content": "28BET 注册送", "author": "a2", "author_id": 2, "is_new_account": True},
        # 成員 3：老帳號，只分享圖片（HTML figure，無文字）×2 —— 被 SQL 粗篩湊進來的真人
        {"content": "<figure><img src='x.png'></figure>", "author": "olduser",
         "author_id": 3, "is_new_account": False},
        {"content": "<figure><img src='y.png'></figure>", "author": "olduser",
         "author_id": 3, "is_new_account": False},
    ]
    # 原 row 假設 n_moments=5（含一則沒抓到的）；成員 3 兩則被剔 → 5-2=3
    row2, items2 = filter_empty_members(row, items, "n_moments")
    assert row2["author_ids"] == [1, 2]
    assert row2["n_authors"] == 2
    assert row2["n_moments"] == 3
    assert row2["new_account_ratio"] == 1.0  # 剩餘成員全是新帳號（重算）
    assert all(str(it["author_id"]) != "3" for it in items2)
    # 原 row 不被就地修改
    assert row["author_ids"] == [1, 2, 3] and row["n_authors"] == 3


def test_filter_empty_members_keeps_member_with_any_text_post():
    """成員只要有任一則有文字的貼文就保留（不是逐貼文剔除，是逐成員判斷）。"""
    row = {"template_fam": "x", "n_articles": 2, "n_authors": 1,
           "new_account_ratio": 0.0, "author_ids": [7]}
    items = [
        {"content": "🎰", "author": "u7", "author_id": 7, "is_new_account": False},
        {"content": "真的有寫字", "author": "u7", "author_id": 7, "is_new_account": False},
    ]
    row2, items2 = filter_empty_members(row, items, "n_articles")
    assert row2 is row and items2 is items  # 無成員被剔 → 原樣返回


def test_filter_empty_members_all_empty_ring_collapses_to_zero():
    row = {"template_fam": "x", "n_moments": 2, "n_authors": 2,
           "new_account_ratio": 0.0, "author_ids": [1, 2]}
    items = [
        {"content": "<p></p>", "author": "a", "author_id": 1, "is_new_account": False},
        {"content": "  ", "author": "b", "author_id": 2, "is_new_account": False},
    ]
    row2, items2 = filter_empty_members(row, items, "n_moments")
    assert row2["n_authors"] == 0 and items2 == []  # detect() 據此整 ring 丟棄


def test_auto_freeze_eligible_double_key():
    def cand(n, ratio, bot):
        return {"nAuthors": n, "newAccountRatio": ratio,
                "signals": {"botUsernameRatio": bot}}
    # 雙鑰成立：跨 3 帳號（F1b 門檻）＋新帳號比高
    assert auto_freeze_eligible(cand(3, 0.9, 0.0))
    # 雙鑰成立：亂碼比高
    assert auto_freeze_eligible(cand(5, 0.5, 0.6))
    # 鑰1 不足（單帳號 F1a 候選天然不合格）
    assert not auto_freeze_eligible(cand(1, 1.0, 1.0))
    assert not auto_freeze_eligible(cand(2, 1.0, 1.0))
    # 老帳號豁免（硬性）：新帳號比低且亂碼低 → 永不自動
    assert not auto_freeze_eligible(cand(40, 0.1, 0.1))
    # 佐證鑰不足：新帳號比中等、亂碼低
    assert not auto_freeze_eligible(cand(10, 0.5, 0.1))
    # 資料缺席即否決
    assert not auto_freeze_eligible(cand(10, None, 0.9))


def test_auto_freeze_only_touches_pending_and_survives_errors(monkeypatch=None):
    calls = []

    def fake_freeze(endpoint, token, ring_id, remark):
        calls.append(ring_id)
        if ring_id == "boom":
            raise RuntimeError("simulated")
        return {"frozen": [{"id": "u1"}], "skipped": []}

    orig = ring_detect_job._post_freeze
    ring_detect_job._post_freeze = fake_freeze
    try:
        def cand(fp):
            return {"fingerprint": fp, "nAuthors": 5, "newAccountRatio": 1.0,
                    "signals": {"botUsernameRatio": 0.6}}
        candidates = [cand("f1"), cand("f2"), cand("f3"), cand("f4"),
                      {"fingerprint": "f5", "nAuthors": 1, "newAccountRatio": 1.0,
                       "signals": {"botUsernameRatio": 1.0}}]  # F1a 單帳號：不合格
        rings = [
            {"id": "r1", "fingerprint": "f1", "status": "pending"},
            {"id": "r2", "fingerprint": "f2", "status": "dismissed"},  # 人工判過誤判 → 不碰
            {"id": "r3", "fingerprint": "f3", "status": "restored"},   # 人工解凍過 → 不碰
            {"id": "boom", "fingerprint": "f4", "status": "pending"},  # 失敗不擋整批
        ]
        s = auto_freeze("http://x", "t", candidates, rings,
                        {"high_authors": 3, "new_ratio_hi": 0.8,
                         "bot_ratio_hi": 0.5, "old_exempt_ratio": 0.34})
    finally:
        ring_detect_job._post_freeze = orig

    assert calls == ["r1", "boom"]  # dismissed/restored/單帳號 全都沒被呼叫
    assert [f["ring_id"] for f in s["frozen"]] == ["r1"]
    assert {x["status"] for x in s["skipped_status"]} == {"dismissed", "restored"}
    assert s["ineligible"] == 1 and len(s["errors"]) == 1


def test_load_sql_substitutes_single_author_min_posts():
    for ct in ("article", "moment"):
        sql = _load_sql(CONTENT_TYPES[ct]["sql"], days=30, min_authors=3,
                        new_account_days=30, single_author_min_posts=3)
        assert ":single_author_min_posts" not in sql
        assert ":min_authors" not in sql and ":days" not in sql
        assert "n_authors = 1" in sql  # F1a 單帳號洗文條款存在


def test_content_queries_carry_is_new_account_flag():
    for spec in CONTENT_TYPES.values():
        assert "is_new_account" in spec["content_query"]
        assert "%(new_account_days)s" in spec["content_query"]


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
