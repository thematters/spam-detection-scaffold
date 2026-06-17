#!/usr/bin/env python3
"""軸一 D 決策引擎（影子模式）——把 ring 候選 CSV 轉成分級處置決策，但**只輸出報告、不動任何帳號**。

輸入：detect_spam_rings.sql / detect_spam_rings_moment.sql 經 VPC runner 寫到 S3 的候選 CSV
      （欄位：template_fam, n_articles|n_moments, n_authors, new_account_ratio,
       sample_authors, [author_ids], earliest_account, latest_post）。

對每個候選 ring，依「雙鑰 + 老帳號豁免」分三級（SPAM_ROADMAP 軸一 D 安全護欄）：
  FREEZE  （高信度，影子模式下＝「會凍結」但不執行）：
          ≥2 把獨立鑰匙成立——
            鑰1 跨帳號重複夠廣（n_authors ≥ HIGH_AUTHORS）
            鑰2 至少一個獨立佐證：新帳號比例高（≥NEW_RATIO_HI）或 帳號名亂碼比例高（≥BOT_RATIO_HI）
  REVIEW  （送人工佇列）：是 ring（n_authors ≥ MIN_AUTHORS）但未達 FREEZE，或落入老帳號豁免。
  SKIP    ：未達 ring 門檻。

**老帳號豁免（硬性）**：新帳號比例 < OLD_EXEMPT_RATIO 且 亂碼比例 < BOT_RATIO_HI 的 ring
（多為長期經營的老帳號，如機械 SEO），**永不自動凍結**，一律 REVIEW——怕誤傷真實老用戶。

⚠️ 影子模式：本腳本不呼叫任何 mutation、不碰帳號。輸出一份「若上線會如何處置」的稽核報告，
   給人工覆核一週、確認不誤傷後，才由處置端（海巡 bot 框架）接真正動作。

用法：
  python ring_decide.py candidates.csv [--out shadow_decisions.json]
  # 門檻可調：--high-authors 5 --new-ratio-hi 0.8 --bot-ratio-hi 0.5 --old-exempt-ratio 0.34 --min-authors 3
"""
from __future__ import annotations
import argparse
import ast
import csv
import json
import re
import sys


def username_bot_score(u: str) -> float:
    """0~1：越高越像自動產生的 throwaway 帳號名（與 ring_detect_poc.py 同口徑，獨立實作免依賴）。"""
    if not u or u == "?":
        return 0.0
    s = 0.0
    digits = sum(c.isdigit() for c in u)
    if digits / len(u) > 0.25:
        s += 0.4
    if re.search(r"[bcdfghjklmnpqrstvwxz]{5,}", u):
        s += 0.3
    if re.search(r"\d{3,}", u):
        s += 0.2
    if len(u) >= 11 and digits >= 2:
        s += 0.1
    return min(s, 1.0)


def _parse_pg_array(v: str) -> list:
    """psql --csv 把 array_agg 輸出成 {a,b,c}；轉成 list。"""
    if not v:
        return []
    v = v.strip()
    if v.startswith("{") and v.endswith("}"):
        inner = v[1:-1]
        return [x.strip().strip('"') for x in inner.split(",") if x.strip()]
    try:
        return list(ast.literal_eval(v))
    except Exception:
        return [v]


def decide(row: dict, cfg) -> dict:
    n_authors = int(row.get("n_authors") or 0)
    n_posts = int(row.get("n_moments") or row.get("n_articles") or 0)
    new_ratio = float(row.get("new_account_ratio") or 0)
    authors = _parse_pg_array(row.get("sample_authors") or "")
    bot_scores = [username_bot_score(a) for a in authors]
    bot_ratio = sum(1 for b in bot_scores if b >= 0.4) / len(bot_scores) if bot_scores else 0.0

    # 鑰匙
    key_spread = n_authors >= cfg.high_authors          # 跨帳號重複夠廣
    key_new = new_ratio >= cfg.new_ratio_hi             # 新帳號比例高
    key_bot = bot_ratio >= cfg.bot_ratio_hi             # 帳號名亂碼比例高
    keys = sum([key_spread, key_new, key_bot])

    # 老帳號豁免（硬性）：幾乎都是老帳號且帳號名正常 → 永不自動，送人工
    old_exempt = (new_ratio < cfg.old_exempt_ratio) and (bot_ratio < cfg.bot_ratio_hi)

    if n_authors < cfg.min_authors:
        action, why = "SKIP", "未達 ring 門檻"
    elif old_exempt:
        action, why = "REVIEW", f"老帳號豁免（新帳號比 {new_ratio:.0%}、亂碼比 {bot_ratio:.0%}）→ 人工"
    elif key_spread and (key_new or key_bot):
        sig = []
        if key_new:
            sig.append(f"新帳號 {new_ratio:.0%}")
        if key_bot:
            sig.append(f"亂碼帳號 {bot_ratio:.0%}")
        action, why = "FREEZE", f"雙鑰成立：跨 {n_authors} 帳號 + {' / '.join(sig)}"
    else:
        action, why = "REVIEW", f"單鑰（跨 {n_authors} 帳號，佐證不足）→ 人工"

    return {"template_fam": row.get("template_fam"), "action": action, "reason": why,
            "n_authors": n_authors, "n_posts": n_posts,
            "new_account_ratio": round(new_ratio, 2), "bot_username_ratio": round(bot_ratio, 2),
            "sample_authors": authors[:10],
            "author_ids": _parse_pg_array(row.get("author_ids") or "")}


def main(argv) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("csv", help="ring 候選 CSV（detect_spam_rings[_moment].sql 的輸出）")
    ap.add_argument("--out", help="影子決策 JSON 輸出路徑")
    ap.add_argument("--min-authors", type=int, default=3, help="列為 ring 的最低跨帳號數")
    ap.add_argument("--high-authors", type=int, default=5, help="FREEZE 鑰1：跨帳號數門檻")
    ap.add_argument("--new-ratio-hi", type=float, default=0.8, help="FREEZE 鑰2a：新帳號比例門檻")
    ap.add_argument("--bot-ratio-hi", type=float, default=0.5, help="FREEZE 鑰2b：亂碼帳號比例門檻")
    ap.add_argument("--old-exempt-ratio", type=float, default=0.34,
                    help="老帳號豁免：新帳號比例低於此且亂碼低 → 永不自動凍結")
    cfg = ap.parse_args(argv[1:])

    rows = list(csv.DictReader(open(cfg.csv)))
    decisions = [decide(r, cfg) for r in rows]

    # 影子彙總
    by = {"FREEZE": [], "REVIEW": [], "SKIP": []}
    for d in decisions:
        by[d["action"]].append(d)
    frozen_accts = set()
    for d in by["FREEZE"]:
        frozen_accts |= set(d["author_ids"])
    review_accts = set()
    for d in by["REVIEW"]:
        review_accts |= set(d["author_ids"])

    print("=" * 64)
    print("軸一 D 影子決策報告（SHADOW — 不執行任何處置）")
    print("=" * 64)
    print(f"候選 ring：{len(rows)}")
    print(f"  FREEZE（會凍結，影子不執行）：{len(by['FREEZE'])} 個 ring"
          f"，涉 {len(frozen_accts)} 帳號")
    print(f"  REVIEW（送人工）：{len(by['REVIEW'])} 個 ring，涉 {len(review_accts)} 帳號")
    print(f"  SKIP：{len(by['SKIP'])} 個")
    print(f"\n--- FREEZE 樣本（最多 12，影子下『會』凍結這些 ring 的帳號）---")
    for d in sorted(by["FREEZE"], key=lambda x: -x["n_authors"])[:12]:
        print(f"  跨{d['n_authors']:>3}帳號 / {d['n_posts']:>3}則 | 新{d['new_account_ratio']:.0%}"
              f" 亂碼{d['bot_username_ratio']:.0%} | {d['reason']}")
        print(f"       e.g. {', '.join(d['sample_authors'][:6])}")
    print(f"\n--- REVIEW 樣本（最多 6，送人工，含老帳號豁免）---")
    for d in sorted(by["REVIEW"], key=lambda x: -x["n_authors"])[:6]:
        print(f"  跨{d['n_authors']:>3}帳號 | 新{d['new_account_ratio']:.0%} 亂碼{d['bot_username_ratio']:.0%}"
              f" | {d['reason']}")

    if cfg.out:
        json.dump({"mode": "SHADOW", "candidates": len(rows),
                   "freeze_rings": len(by["FREEZE"]), "freeze_accounts": sorted(frozen_accts),
                   "review_rings": len(by["REVIEW"]), "review_accounts": sorted(review_accts),
                   "decisions": decisions}, open(cfg.out, "w"), ensure_ascii=False, indent=2)
        print(f"\nwrote {cfg.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
