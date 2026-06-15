#!/usr/bin/env python3
"""驗證標註引擎：拿人工手判的 136 筆 gold（2026-06-15 acceptance 誤判個案）測 LLM-judge 對齊率。

**規模化前必過這關**：LLM 標註器若無法重現人工判斷，就不能信任它批量標。
重點指標：
  - 保護類 recall：gold=ham（12 筆華語政治/學術/日記/金融科普）有多少被正確標 ham。
    這是矯正偏差的關鍵——標錯成 spam 等於把模型偏差又學回去。
  - spam precision/recall：gold=spam（124 筆外語 SEO/博弈/色情）的對齊。
  - 整體 accuracy + 混淆矩陣 + 逐筆 disagreement。

資料源（皆 S3，2026-06-15）：
  gold 標籤：article-label-corrections/2026-06-15/judged_misfires.csv
  內文片段：conformal-acceptance/2026-06-15/result.json 的 misfires[].snippet

用法：python validate_labeler.py --backend bedrock
"""
from __future__ import annotations
import argparse, csv, io, json, subprocess, sys
from collections import Counter

import llm_label_articles as L

BUCKET = "matters-spam-training-samples"
GOLD_KEY = "article-label-corrections/2026-06-15/judged_misfires.csv"
SNIP_KEY = "conformal-acceptance/2026-06-15/result.json"


def _s3_text(key: str) -> str:
    return subprocess.check_output(
        ["aws", "s3", "cp", f"s3://{BUCKET}/{key}", "-", "--region", "ap-southeast-1"]).decode()


def load_gold():
    gold = {}   # id -> {label, title}
    for row in csv.DictReader(io.StringIO(_s3_text(GOLD_KEY))):
        gold[int(row["article_id"])] = {"label": row["judged_label"], "title": row["title"]}
    snippets = {}
    for m in json.loads(_s3_text(SNIP_KEY)).get("misfires", []):
        snippets[m["article_id"]] = m.get("snippet") or ""
    arts = []
    for aid, g in gold.items():
        if g["label"] not in ("ham", "spam"):
            continue   # review 類不納入對齊計算
        arts.append({"article_id": aid, "title": g["title"], "text": snippets.get(aid, ""),
                     "gold": g["label"]})
    return arts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", choices=["auto", "bedrock", "anthropic"], default="auto")
    ap.add_argument("--min-conf", type=float, default=0.0, help="驗證階段不轉 review，直接看 model_label")
    args = ap.parse_args()

    arts = load_gold()
    n_ham = sum(1 for a in arts if a["gold"] == "ham")
    print(f"gold：{len(arts)} 筆（ham={n_ham} / spam={len(arts) - n_ham}）")

    try:
        complete = L.make_completer(args.backend)
    except Exception as e:
        print(f"❌ LLM 後端不可用：{e}\n（需開通 Bedrock Anthropic 存取或設 ANTHROPIC_API_KEY）")
        return 2

    cm = Counter()        # (gold, pred)
    disagree = []
    for a in arts:
        r = L.label_one(complete, a, args.min_conf)
        pred = r["model_label"] or "parse_fail"
        cm[(a["gold"], pred)] += 1
        if pred != a["gold"]:
            disagree.append((a["gold"], pred, r.get("confidence"), a["title"][:50], r.get("reason")))

    # 指標
    def g(go, pr): return cm.get((go, pr), 0)
    ham_recall = g("ham", "ham") / max(1, n_ham)
    spam_recall = g("spam", "spam") / max(1, len(arts) - n_ham)
    acc = (g("ham", "ham") + g("spam", "spam")) / max(1, len(arts))
    print(f"\n=== 對齊結果（vs 人工 gold）===")
    print(f"整體 accuracy：{acc:.1%}")
    print(f"保護類 recall（華語 ham 正確標 ham）：{ham_recall:.1%}  ← 最關鍵，越高越能矯正偏差")
    print(f"spam recall（spam 正確標 spam）：{spam_recall:.1%}")
    print(f"混淆矩陣：ham→ham={g('ham','ham')} ham→spam={g('ham','spam')} | "
          f"spam→spam={g('spam','spam')} spam→ham={g('spam','ham')} | 解析失敗={g('ham','parse_fail')+g('spam','parse_fail')}")
    print(f"\n=== 不一致個案（{len(disagree)}）===")
    for go, pr, conf, title, reason in disagree[:25]:
        print(f"gold={go} pred={pr} conf={conf} 《{title}》 — {reason}")
    print("\n判讀：保護類 recall ≥ ~0.9 才可信任批量標華語內容；ham→spam 的誤標要逐筆檢視。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
