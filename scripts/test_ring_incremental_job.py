"""ring_incremental_job 純函式測試（不碰 DB/S3/HTTP）。
可用 pytest 或 `python scripts/test_ring_incremental_job.py`。"""
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ring_incremental_job import (  # noqa: E402
    drop_processed_members,
    group_to_sql_row,
    plan_candidates,
    to_state_row,
    watermark_of,
)
import ring_signals  # noqa: E402

NOW = dt.datetime(2026, 7, 4, 12, 0, tzinfo=dt.timezone.utc)


def _row(pid, author, fp, created="2026-07-04T10:00:00+00:00",
         user_created="2026-07-01T00:00:00+00:00"):
    return {"id": pid, "author_id": author, "author_name": f"u{author}",
            "user_created_at": user_created, "created_at": created, "fp": fp}


def test_to_state_row_drops_empty_content_posts():
    # 純圖/emoji/url 貼文不進狀態 → 永遠不構成 ring（F3a 每貼文版）
    assert to_state_row({"id": 1, "author_id": 2, "author_name": "a",
                         "user_created_at": None, "created_at": None,
                         "content": "<figure><img src='x.png'></figure>"}) is None
    r = to_state_row({"id": 1, "author_id": 2, "author_name": "a",
                      "user_created_at": dt.datetime(2026, 7, 1),
                      "created_at": dt.datetime(2026, 7, 4),
                      "content": "28BET 注册送"})
    assert r["id"] == "1" and r["author_id"] == "2"
    assert r["fp"] == ring_signals.normalized_fingerprint("28BET 注册送")
    assert r["created_at"].startswith("2026-07-04")


def test_watermark_bootstrap_and_normal():
    # 冷啟動：狀態空 → 只回看 bootstrap 窗
    wm = watermark_of([], bootstrap_hours=24, now=NOW)
    assert wm == NOW - dt.timedelta(hours=24)
    # 正常：取狀態中最新貼文時間
    rows = [_row("1", "a", "f1", created="2026-07-03T00:00:00+00:00"),
            _row("2", "a", "f1", created="2026-07-04T09:30:00+00:00")]
    assert watermark_of(rows, bootstrap_hours=24, now=NOW).isoformat().startswith(
        "2026-07-04T09:30")


def test_plan_candidates_only_touched_fingerprints():
    state = [
        _row("1", "a", "ring1"), _row("2", "b", "ring1"),
        # ring2：3 帳號但沒被新貼文碰到 → 不重算
        _row("3", "c", "ring2"), _row("4", "d", "ring2"), _row("5", "e", "ring2"),
    ]
    new = [_row("6", "c2", "ring1")]  # ring1 被碰到 → 3 帳號成 ring
    groups = plan_candidates(state + new, new, min_authors=3, single_author_min_posts=3)
    assert [g["fp"] for g in groups] == ["ring1"]
    assert groups[0]["n_authors"] == 3 and set(groups[0]["member_ids"]) == {"a", "b", "c2"}


def test_plan_candidates_single_author_rule_and_dedupe():
    new = [_row("1", "x", "solo"), _row("2", "x", "solo"), _row("3", "x", "solo"),
           _row("3", "x", "solo")]  # 重複 post id 去重
    groups = plan_candidates(new, new, min_authors=3, single_author_min_posts=3)
    assert len(groups) == 1 and groups[0]["n_authors"] == 1 and groups[0]["n_posts"] == 3
    # 未達單帳號門檻 → 無候選
    assert plan_candidates(new[:2], new[:2], min_authors=3, single_author_min_posts=3) == []


def test_drop_processed_members_removes_frozen_and_rechecks():
    g = {"fp": "f", "rows": [_row("1", "a", "f"), _row("2", "b", "f"), _row("3", "c", "f")],
         "member_ids": ["a", "b", "c"], "post_ids": ["1", "2", "3"],
         "n_posts": 3, "n_authors": 3}
    g2 = drop_processed_members(g, {"a": "frozen", "b": "active", "c": "active"})
    assert g2["n_authors"] == 2 and g2["member_ids"] == ["b", "c"]
    # 全滅 → None
    assert drop_processed_members(g, {"a": "frozen", "b": "banned", "c": "archived"}) is None
    # 無處置成員 → 原物返回
    assert drop_processed_members(g, {"a": "active"}) is g


def test_group_to_sql_row_new_account_ratio_per_author():
    rows = [
        _row("1", "new1", "f", user_created="2026-07-01T00:00:00+00:00"),   # 3 天齡＝新
        _row("2", "new1", "f", user_created="2026-07-01T00:00:00+00:00"),   # 同帳號第二篇不重複計票
        _row("3", "old1", "f", user_created="2025-01-01T00:00:00+00:00"),   # 老帳號
    ]
    g = {"fp": "f", "rows": rows, "member_ids": ["new1", "old1"],
         "post_ids": ["1", "2", "3"], "n_posts": 3, "n_authors": 2}
    row = group_to_sql_row(g, "n_moments", new_account_days=30, now=NOW)
    assert row["n_moments"] == 3 and row["n_authors"] == 2
    assert row["new_account_ratio"] == 0.5  # 每帳號一票：1 新 / 2 帳號
    assert row["template_fam"] == "f" and row["author_ids"] == ["new1", "old1"]


def test_naive_db_timestamps_are_coerced_to_utc_aware():
    """replica 的 timestamp 欄位是 naive——序列化時補 tz，之後全在 aware 世界比較
    （回歸：naive/aware 混比會 TypeError）。"""
    r = to_state_row({"id": 9, "author_id": 9, "author_name": "n",
                      "user_created_at": dt.datetime(2026, 7, 1, 3, 0),   # naive
                      "created_at": dt.datetime(2026, 7, 4, 3, 0),        # naive
                      "content": "有字"})
    assert r["created_at"].endswith("+00:00") and r["user_created_at"].endswith("+00:00")
    # watermark / ratio 都能和 aware now 相比，不炸
    assert watermark_of([r], bootstrap_hours=24, now=NOW) < NOW
    g = {"fp": r["fp"], "rows": [r], "member_ids": ["9"], "post_ids": ["9"],
         "n_posts": 1, "n_authors": 1}
    row = group_to_sql_row(g, "n_moments", new_account_days=30, now=NOW)
    assert row["new_account_ratio"] == 1.0


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
