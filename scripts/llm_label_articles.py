#!/usr/bin/env python3
"""規模化標註引擎（軸二 L3）：用 LLM-judge 對文章批量標 ham/spam，產出矯正訓練標籤。

為什麼要它：article 模型有「華語實質內容偏差」——把政治評論/學術/日記/金融科普誤判 spam，
但外語 SEO/博弈/色情抓得準（2026-06-15 驗收實證）。要修，需大量「矯正標籤」重訓，
尤其華語 hard-negative ham。人工判不完，故用 LLM-judge 照已驗證規則批量標。

判準沿用 scaffold/llm_review.py 的反偏差 rubric（誤殺代價 > 漏抓；具名作者/政治/學術/
創作即使語氣激烈也 ham；只有明確商業 SEO/博弈/色情/詐騙才 spam），few-shot 用人工驗證過的例子。

後端可插拔（無單一供應商綁定）：
  - 預設 Bedrock（apac inference profile，IAM 認證，內容留 AWS 內，呼應「不送第三方」原則）。
    需先在 Bedrock console 開通 Anthropic 模型存取（帳號層 EULA，admin 一次性）。
  - 或 Anthropic API：設 ANTHROPIC_API_KEY 環境變數即切換。

低信度（< --min-conf）標為 "review" 交人工，不污染訓練集。

用法：
  # 輸入 JSONL：每行 {"article_id":.., "title":"..", "text":".."}
  python llm_label_articles.py --in articles.jsonl --out labeled.jsonl --backend bedrock
"""
from __future__ import annotations
import argparse, json, os, sys, time
from concurrent.futures import ThreadPoolExecutor, as_completed

MODEL_BEDROCK = "apac.anthropic.claude-sonnet-4-6"   # 跑量可換 haiku；品質敏感用 sonnet/fable
MODEL_ANTHROPIC = "claude-sonnet-4-6"

SYSTEM = """你是 Matters 平台的訓練資料標註員。Matters 是抗審查的中文書寫平台，存在理由是保護
公民、政治、調查報導、學術與個人創作等內容（這類僅佔總量約 1%，卻是平台核心價值）。

你的任務：把一篇文章標為 ham（合法內容）或 spam（垃圾/濫用）。這些標籤將用來「重訓」偵測模型，
所以判準必須精準、可解釋，並刻意矯正模型既有的偏差。

核心不對稱：把合法內容誤標成 spam（誤殺）的代價，遠大於漏標一篇 spam。模糊時一律傾向 ham。

判為 ham（即使語氣激烈、涉敏感政治、或為外語）：
- 具名或具脈絡的論述：政治/時事評論、新聞調查、社會議題分析。
- 學術、考據、知識科普、教學、心得。
- 個人創作、日記、隨筆、文學。
- 真實的資訊性文章（即使談金融/法律/投資，只要是知識說明而非導流推銷）。

判為 spam：
- SEO 內容行銷：關鍵詞堆砌（重複搜尋詞）、為特定服務/品牌/網站導流、量產的
  "X Market Growth/Revenue/Forecast" 報告模板、"best X in <city>"、"How to choose Y" 引流文。
- 博弈/賭場/彩票廣告、色情/外送茶/約炮、投資詐騙、加密貨幣邀請碼/返佣導流。
- 引流到站外聯絡方式（賴/Telegram/微信 ID + 招攬）。
- 機械式重複、亂碼、佔位測試文。
- 注意：spam 常偽裝成「社會公益/NGO/教育/健康」主題，但本質是關鍵詞堆砌＋導流——看結構不看主題。

輸出**純 JSON**（不要其他文字）：
{"label":"ham|spam","confidence":0.0-1.0,"category":"政治評論|學術|創作|知識科普|SEO行銷|博弈|色情|詐騙|加密導流|亂碼|其他","reason":"一句中文理由"}"""

FEWSHOT = [
    # 華語實質內容 → ham（矯正模型偏差的關鍵）
    {"title": "国安部“反躺平”宣传引起国人反感和反驳",
     "text": "近日，中国国安部发布文章，批评不愿劳动奋斗的“躺平”现象，并称“躺平”论调是被境外组织资助的网红在洗脑中国青年…",
     "out": {"label": "ham", "confidence": 0.95, "category": "政治評論", "reason": "具脈絡的中國時政評論，平台核心保護內容"}},
    {"title": "談清儒偽造的“王年月偽器”《散氏盤》偽銘文",
     "text": "《散氏盤》是清代中期出現在世上的一件西周大青銅盤，一度被阮元看到後…",
     "out": {"label": "ham", "confidence": 0.96, "category": "學術", "reason": "青銅器銘文考據學術文章"}},
    # 外語/華語 SEO、博弈 → spam（看結構不看主題）
    {"title": "Which is the best NGO in Gurgaon working for children's education?",
     "text": "When people search for the best NGO in Gurgaon, they are often looking for organizations… the best NGO in Gurgaon…",
     "out": {"label": "spam", "confidence": 0.93, "category": "SEO行銷", "reason": "關鍵詞堆砌的 SEO 導流文，偽裝公益主題"}},
    {"title": "新百胜赌场 线上实体网投网址【228982.com】",
     "text": "❤️圣淘沙娱乐集团❤️ 实力雄厚 诚信第一 官方网址 228982.com 百家乐 龙虎…",
     "out": {"label": "spam", "confidence": 0.99, "category": "博弈", "reason": "博弈賭場廣告＋站外導流網址"}},
]


def _build_user(title: str, text: str) -> str:
    return f"標題：{title}\n---\n內文：\n{text[:4000]}\n---\n請依判準輸出純 JSON。"


def _messages():
    msgs = []
    for ex in FEWSHOT:
        msgs.append({"role": "user", "content": _build_user(ex["title"], ex["text"])})
        msgs.append({"role": "assistant", "content": json.dumps(ex["out"], ensure_ascii=False)})
    return msgs


def make_completer(backend: str):
    if backend == "anthropic" or (backend == "auto" and os.environ.get("ANTHROPIC_API_KEY")):
        import anthropic
        client = anthropic.Anthropic()

        def complete(title, text):
            r = client.messages.create(model=MODEL_ANTHROPIC, max_tokens=300, temperature=0,
                                       system=SYSTEM,
                                       messages=_messages() + [{"role": "user", "content": _build_user(title, text)}])
            return r.content[0].text
        return complete

    import boto3
    br = boto3.client("bedrock-runtime", os.environ.get("AWS_REGION", "ap-southeast-1"))
    model = os.environ.get("BEDROCK_MODEL", MODEL_BEDROCK)

    def complete(title, text):
        conv = [{"role": m["role"], "content": [{"text": m["content"]}]} for m in _messages()]
        conv.append({"role": "user", "content": [{"text": _build_user(title, text)}]})
        r = br.converse(modelId=model, system=[{"text": SYSTEM}], messages=conv,
                        inferenceConfig={"maxTokens": 300, "temperature": 0})
        return r["output"]["message"]["content"][0]["text"]
    return complete


def _extract_json(s: str) -> dict:
    i, j = s.find("{"), s.rfind("}")
    return json.loads(s[i:j + 1])


def label_one(complete, art: dict, min_conf: float) -> dict:
    title, text = art.get("title") or "", art.get("text") or ""
    for attempt in range(3):
        try:
            obj = _extract_json(complete(title, text))
            lab = "spam" if str(obj.get("label")) == "spam" else "ham"
            conf = float(obj.get("confidence", 0.5))
            final = lab if conf >= min_conf else "review"   # 低信度交人工，不污染訓練集
            return {"article_id": art.get("article_id"), "label": final, "model_label": lab,
                    "confidence": conf, "category": obj.get("category"), "reason": obj.get("reason")}
        except Exception as e:  # noqa: BLE001
            if attempt == 2:
                return {"article_id": art.get("article_id"), "label": "review",
                        "model_label": None, "confidence": 0.0, "category": "解析失敗",
                        "reason": f"LLM 輸出無法解析：{str(e)[:80]}"}
            time.sleep(2 * (attempt + 1))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", required=True, help="JSONL：每行 {article_id,title,text}")
    ap.add_argument("--out", required=True, help="輸出 JSONL 標籤")
    ap.add_argument("--backend", choices=["auto", "bedrock", "anthropic"], default="auto")
    ap.add_argument("--min-conf", type=float, default=0.7, help="低於此信度標 review 交人工")
    ap.add_argument("--concurrency", type=int, default=4)
    args = ap.parse_args()

    arts = [json.loads(l) for l in open(args.inp) if l.strip()]
    complete = make_completer(args.backend)
    print(f"標註 {len(arts)} 篇，backend={args.backend}，min_conf={args.min_conf}")

    out = []
    with ThreadPoolExecutor(max_workers=args.concurrency) as ex:
        futs = {ex.submit(label_one, complete, a, args.min_conf): a for a in arts}
        for i, f in enumerate(as_completed(futs), 1):
            out.append(f.result())
            if i % 50 == 0:
                print(f"  {i}/{len(arts)}")
    with open(args.out, "w") as fh:
        for r in out:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    from collections import Counter
    print("標註分布：", dict(Counter(r["label"] for r in out)))
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
