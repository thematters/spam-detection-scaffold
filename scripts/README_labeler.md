# 規模化標註引擎（軸二 L3）

修 article 模型的「華語實質內容偏差」需要大量矯正標籤重訓。人工判不完，故用 LLM-judge
照已驗證規則批量標 ham/spam。

## 為什麼需要（2026-06-15 驗收實證）
乾淨 held-out（老帳號）acceptance：模型把**華語政治評論/學術/日記/金融科普**以 0.98–1.0
高信度誤判 spam（conformal 救不了高信度誤判），但對**外語 SEO/博弈/色情**判得準。
→ 需用矯正標籤（尤其華語 hard-negative ham）重訓。判準與例子來自人工驗證的 136 筆。

## 元件
- `llm_label_articles.py` — 標註器。沿用 `llm_review.py` 反偏差 rubric（誤殺代價>漏抓、
  具名/政治/學術/創作即使語氣激烈也 ham、看結構不看主題抓 SEO/博弈/色情），few-shot 用驗證過的例子。
  後端可插：Bedrock（預設，IAM、內容留 AWS 內）或 Anthropic API（設 `ANTHROPIC_API_KEY`）。
  低信度（<`--min-conf`）標 `review` 交人工，不污染訓練集。
- `validate_labeler.py` — **規模化前必過的關卡**：拿人工 gold（136 筆）測對齊率，
  重點是「保護類 recall」（華語 ham 正確標 ham）。

## 依賴（一次性，需 admin）
⚠️ **Bedrock Anthropic 模型存取目前未開通**（帳號層 `ValidationException: Access to Anthropic
models is not allowed`）。需 admin 至 Bedrock console → Model access 開通 Anthropic 系列
（接受 EULA）。或改用 Anthropic API（設 `ANTHROPIC_API_KEY`）。

## 流程
```bash
# 1) 驗證（開通後第一件事）——保護類 recall 要 ≥ ~0.9 才可信任
python scripts/validate_labeler.py --backend bedrock

# 2) 過關後批量標（輸入 JSONL：{article_id,title,text}，來源=replica 撈的候選文章）
python scripts/llm_label_articles.py --in candidates.jsonl --out labeled.jsonl

# 3) labeled.jsonl 的 ham/spam → 餵 train_rebalanced.py 重訓；review → 人工
```
規模化執行可加進 `infra/vpc-runner/buildspec.yml` 一個 `label` JOB（VPC runner 撈 replica
候選 → 標註 → 寫 S3），與 ring/acceptance/l1 並列。**但須先過 validate。**

## 安全立場
serving-time 覆核仍應走容器內小模型（不送用戶內容出去）；本引擎是**離線**訓練資料標註，
用 Bedrock（你們 AWS 內）可接受。低信度一律轉人工。
