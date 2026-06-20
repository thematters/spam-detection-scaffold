"""eval/ring_signals.py 的純單元測試（不打網路）。
可用 pytest 跑，也可直接 `python eval/test_ring_signals.py` 跑。"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ring_signals import (  # noqa: E402
    advertised_entities, MAINSTREAM_DOMAINS, template_family,
    top_entity_ring, username_bot_score,
)


# --- 邀請碼 token（破口①：crypto 返佣 ring 的真正不變量）---
def test_invite_code_simplified_bracketed():
    assert "invite:OK183" in advertised_entities("欧易交易所注册【欧易邀请码OK183】快来")


def test_invite_code_traditional_colon():
    assert "invite:BIN6666" in advertised_entities("幣安【邀請碼：BIN6666】註冊送好禮")


def test_invite_code_english():
    assert "invite:EA888" in advertised_entities("register with invitation code EA888 today")


def test_invite_code_letters_only():
    # 純字母碼（實測 LIDANG）也要抓到，不能只認字母+數字
    assert "invite:LIDANG" in advertised_entities("币安邀请码 LIDANG 返佣最高")


def test_invite_code_rebate_traditional():
    assert "invite:3XCB" in advertised_entities("Bitget 代理商返佣碼 3XCB 註冊")


# --- 品牌詞（破口②：博弈/交易所 ring 的真正不變量）---
def test_brand_words():
    e = advertised_entities("28BET 信誉平台，也玩 欧易 OKX 与 bitget")
    assert {"brand:28bet", "brand:欧易", "brand:okx", "brand:bitget"} <= e


# --- 主流網域白名單（防 28BET 誤 key 在反向連結上）---
def test_mainstream_domain_excluded_but_spam_kept():
    e = advertised_entities("follow youtube.com/xx and pinterest.com, join 28bet99.vip now")
    assert "youtube.com" not in e and "pinterest.com" not in e  # 主流平台排除
    assert "28bet99.vip" in e                                   # 賭場網域保留


def test_telegram_not_allowlisted():
    # Telegram 是 spam 聯絡管道，不可進主流白名單（否則漏掉聯絡訊號）
    assert "t.me" not in MAINSTREAM_DOMAINS


# --- template_family 遮數字讓同模板歸群 ---
def test_template_family_masks_digits():
    assert template_family("邀请码 BIN6666 注册") == template_family("邀请码 BIN8888 注册")


# --- entity ring 計跨帳號數（去重作者）---
def test_top_entity_ring_counts_distinct_authors():
    items = [
        {"content": "邀请码 LIDANG", "author": "a"},
        {"content": "用邀请码 LIDANG 注册", "author": "b"},
        {"content": "邀请码 LIDANG 返佣", "author": "a"},  # 重複作者不重複計
    ]
    ent, n = top_entity_ring(items)
    assert ent == "invite:LIDANG" and n == 2


# --- 帳號名亂碼分數 ---
def test_username_bot_score():
    assert username_bot_score("owvrhgip") >= 0.3   # 長子音串 wvrhg
    assert username_bot_score("user12345") >= 0.4  # 數字比例高
    assert username_bot_score("alice") == 0.0


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn(); print(f"  PASS {fn.__name__}")
        except AssertionError as e:
            failed += 1; print(f"  FAIL {fn.__name__}: {e}")
    print(f"\n{len(fns)-failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
