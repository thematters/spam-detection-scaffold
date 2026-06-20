"""ring_detect_job 純函式測試（不碰 DB/HTTP；psycopg/requests 在函式內才 import）。
可用 pytest 或 `python scripts/test_ring_detect_job.py`。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ring_detect_job import assemble_signals, build_candidate, _severity_of  # noqa: E402


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
    assert c["fingerprint"] == "abc12345"
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
