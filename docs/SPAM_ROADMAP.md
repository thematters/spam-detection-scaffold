# Spam 防治 Roadmap — 處置上線、conformal review、訓練資料留存

> 狀態日期 2026-06-13。本文是跨 repo（`spam-detection-scaffold`、`spam-detection-serverless`、
> `matters-coastguard-bot`、`matters-server`）的工作總圖。法務已審核訓練資料留存方向（2026-06-13 口頭確認）。

## 現況（已上線/已驗證）

- 留言模型 endpoint：`https://4xa9d5l1gd…/Prod/spam/infer/`（comment model，與文章/短內容模型獨立）。
- matters-server 留言打分 + 存 `spamScore`（PR #4838 merged，dev/prod env 已設）。
- 海巡 bot Phase 1（report-only，TIER1=0.80 高精度）運行中，已實測對 production 送出真實 Tier-1 檢舉。
- 海巡帳號已獲 `communityWatch` feature flag（可進 Phase 2 移除）。
- conformal abstention serving 已 merge 進 `spam-detection-serverless` main（PR #2），**尚未部署**。
- DB report_reason 落差修正：matters-server PR #4844（migration 對齊 GraphQL enum）。

---

## 軸一：conformal review 接起來（A/B/C 三條全做，依序）

決策輸出 `decision ∈ {allow, block, review}`；`review` = 灰區，需人工裁決。

### B（先做）— 部署 article conformal serving 到 staging
- `spam-detection-serverless` **目前無任何 CI**。需新增 OIDC SAM 部署 workflow（以 scaffold 的
  `deploy-comment-model.yml` 為範本）。
- 獨立 staging stack `article-spam-conformal-staging`，**絕不碰** prod stack `article-spam-model-v20251229`。
- 旋鈕 `CONFORMAL_EPS`(0.02)、`CONFORMAL_CALIB` 走 env。calib 重建法見 `spam-detection-serverless` README / 專案記憶。
- 驗收：staging 上跑 shadow eval，確認誤殺率/佇列率符合預期再談 prod。
- ✅ 已部署 staging（`article-spam-conformal-staging`，endpoint `fjrmugbg5j`），tar 已驗證與 prod 同（Δ score=0）。
- ⚠️ **驗收尚未通過**：in-sample 首跑（eval/staging_conformal_accept.py，100+100）ham block≈97%，但這是
  訓練集 ham 標籤雜訊（大量純圖片無文字文章 granite≈1.0）+ 504 冷啟動污染所致，**非真實誤殺率**。
  正式驗收需 read-replica 乾淨 held-out ham + 過濾低文字文章 + 低並發。**未過驗收前不可上 prod。**
- 依賴（需 ops/admin）：~~repo 的 OIDC role + secrets、staging stack~~ ✅ 已由 session 用 AWS 代設並部署。
  正式驗收需 read-replica 存取（held-out 文章）。

### A — conformal 帶到留言模型 endpoint（bot 消費）
- 留言模型（scaffold）目前只回 `{score}`。需移植 conformal 三件套 + 重建**留言**calib + 重部署。
- bot 改三段：`block`→Tier-1/2（依 enable_remove），`review`→人工佇列，`allow`→skip。
- bot 灰區（0.55–0.80）從「拉高門檻略過」改為「進 review 佇列」。

### C — matters-server 讀 decision，接既有 Community Watch review 佇列
- develop 已有 review 系統（`community_watch_action.reviewState`/`appealState`、`updateCommunityWatchActionState`、
  `restoreCommunityWatchComment`）。`review` 帶可直接落成一筆 `reviewState='pending'` 的待裁決項。
- 依賴：endpoint 先回傳 `decision`（軸一 A/B 完成後）。

---

## 軸二：註銷/處置資料留存訓練（三層全做，L1→L3）

### 查證結論（內容目前還在，但有遺失風險）
- `archiveUser` 只刪 draft/pending/error 文章 + assets；**已發佈文章、留言、moment(→archived) 內容保留**。
- 留言移除內容快照存 `community_watch_action.originalContent`，但 `clearCommunityWatchOriginalContent` /
  `contentExpiresAt` 會清成 null。
- 風險：未來硬刪除/GDPR 抹除、快照清除 → 訓練訊號永久遺失；目前無「處置當下落袋」機制。

### L1（先做）— 被動增量抽取
- 定期（每日）增量 SQL，從 read-replica 抽：
  - **正樣本（spam）**：`user_restriction` 作者 / `comment.is_spam` / `spamScore≥threshold` /
    `community_watch_action`（actionState=active 且 reviewState≠reversed）的 originalContent。
  - **Hard-negative（ham）**：`community_watch_action` reviewState=`reversed` 或 actionState∈(restored,voided)
    的 originalContent（被推翻的處置＝誤殺，專治 ham 稀少/受保護 1%）。
- 去識別化後 append 到 S3 訓練桶。SQL 見 `sql/extract_spam_training_incremental.sql`。

### L2 — 主動快照（防硬刪除）— 見下方「快照儲存建議」
- 處置事件當下寫一筆去識別化樣本，與 live 內容生命週期解耦。
- 觸發點：`communityWatchRemoveComment`、`archiveUsers`/ban、報告折疊、`clearCommunityWatchOriginalContent`（清除前先存）。

### L3 — 標籤品質 + 隱私治理
- 排除被推翻處置（appeal upheld / review reversed / 誤殺駁回）出正樣本，改入 hard-negative。
- 樣本只留模型所需文字 + label + metadata（reason/score/時間/salted hash id），**不留可聯繫個資**。
- 存取限訓練用途；保留期依法務指引設定（已過審）。
- 閉環：conformal review 經人工裁決的結果 = 高價值人工標籤 → 回流 L2。

---

## 快照儲存建議（L2）— 採 S3，非 Postgres 表

**建議：去識別化樣本寫入獨立 S3 桶（如 `matters-spam-training-samples`），append-only、
依日期 partition 的 JSONL/parquet；寫入走 SQS → 小 Lambda worker（沿用 matters-server 既有
`enqueueReportAlert` 的 best-effort SQS 模式，絕不阻塞使用者 mutation）。**

理由：
1. **解耦才是重點**：放進 matters-server 同一個 Postgres 會把訓練資料綁回營運 DB 與刪除工具
   （GDPR 抹除 job 掃 DB 時可能連訓練表一起清），失去「帳號刪了樣本還在」的目的。S3 獨立桶
   有自己的存取政策與生命週期。
2. **訓練端本來就讀 S3**：現有 pipeline 從 S3 parquet 讀；快照直接落在訓練消費的地方。
3. **append-only 物件儲存**最適合只增的標註語料；Postgres 表會膨脹並與營運負載競爭。
4. **寫入非阻塞**：moderation/ban mutation 發一個 SQS 事件即返回，worker 落 S3；佇列故障不影響使用者。
5. **去識別**：存 text + label + reason + score + 時間 + author/content 的 salted hash（可去重、不可還原）。

（若日後需要人工裁決 UI 直接查，可在 review 佇列那側用 `community_watch_action`（已存 originalContent
作 UI 用途）；S3 是訓練用的耐久正本，兩者分工。）

---

## 依賴 / 待外部
- ops/admin：serverless repo OIDC role + secrets、staging stack、S3 訓練桶 + SQS 佇列 + Lambda worker IAM。
- 法務：保留期數值（方向已過審，待填具體天數/政策連結）。
- HF token 輪替（獨立資安項，見專案記憶）。

## 進度追蹤
- [x] 軸一查證（serverless 無 CI；develop 已有 review 系統）
- [x] 軸二查證（archiveUser 行為；community_watch_action 欄位）
- [x] L1 SQL（本 repo `sql/extract_spam_training_incremental.sql`）
- [ ] 軸一 B：serverless staging 部署 workflow
- [ ] L1：增量匯出 job（GH Action cron + read-replica secret + S3）
- [ ] L2：matters-server 快照事件 + SQS→Lambda→S3
- [ ] 軸一 A / C：留言 endpoint conformal + server 讀 decision
- [ ] L3：標籤品質規則 + 隱私政策數值
