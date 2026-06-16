# Matters Spam 防治 — 進度報告

> 期間：2026-06-13 ～ 2026-06-16　｜　範圍：留言層 + 文章層 防治，從偵測到處置到模型矯正上線
> 跨 repo：`spam-detection-serverless`、`spam-detection-scaffold`、`matters-coastguard-bot`、`matters-server`

---

## 0. 總覽：現在線上有什麼

| 防治對象 | 偵測 | 處置 | 狀態 |
| --- | --- | --- | --- |
| **留言** | 留言 spam 模型（endpoint `4xa9d5l1gd`，門檻移除 0.80 / 檢舉 0.55） | 海巡 bot 三層（檢舉 / 自動打掃 / Telegram 通知）+ server 端三層 alert | ✅ **PROD** |
| **文章** | 文章模型 **v1（標籤矯正重訓）** on prod（endpoint `ps349rjby5`）+ conformal | `spamScore ≥ 0.94` → 排除出推薦/熱門/搜尋（「不刪除只是不再被看見」） | ✅ **PROD（新文章即時生效）** |
| **帳號層 ring** | template_family 跨帳號重複（軸一 D） | 海巡 bot Phase 2 ring gate 自動打掃 | ✅ **PROD** |

兩條防治線的**模型與偵測都已 live on prod**。文章誤殺修復對**新內容**即時生效。

---

## 1. 留言層（comment）

### 偵測 + 打分
- 留言 spam 模型部署為 serverless endpoint `4xa9d5l1gd`（與文章/短內容模型獨立）。
- matters-server 留言打分 + 存 `spamScore`（PR #4838），dev/prod env 已設。

### 處置一：海巡 bot（`matters-coastguard-bot`，GitHub Actions 排程）
- Phase 1（report-only，TIER1=0.80 高精度）→ 實測對 prod 送出真實 Tier-1 檢舉。
- **Phase 2 自動打掃啟用**（`COASTGUARD_ENABLE_REMOVE=true`）：`communityWatchRemoveComment`（隱藏 + 自動寫一筆檢舉）。
- 觸發＝(a) **ring gate**（軸一 D，`bot/ring.py`：從守望相助移除歷史建 template_family index，候選命中 ≥3 筆已確認類似移除即打掃，**不看模型分數**，專抓模型漏的 ring）或 (b) score ≥ 0.80。
- 護欄：reversed-rate 煞車（>0.10 停手）、MAX_ACTIONS=20/run、可逆（`restoreCommunityWatchComment` + 申訴）。
- 過程修三個坑：workflow dispatch 預設覆蓋 repo variable、threshold 帶空、預設 reason `community_watch_porn_ad` 不被 DB constraint 接受（改 `illegal_advertising`）。

### 處置二：server 端三層 alert（matters-server #4851，notify-only）
- 用直連 prod 資料校門檻定出三層原則：**A(auto)** 複合閘 `score≥threshold ∧ 含聯絡方式 ∧ 含招攬詞`（實測對真實高分集 0 誤判）；**B(ring)** 作者近重複；**C(review)** 高分但非 A/B → 送人工，永不自動處置。
- `detectSpam → _alertSpamIfHighScore`，重用既有 report-alert SQS → Telegram worker（新 source `spam_detection`）。閘 gated by `MATTERS_COMMENT_SPAM_ALERT`（預設關）。**永不隱藏留言**。
- 純邏輯在 `commentSpamSignals.ts`（regex + char-3gram Jaccard），對真實 prod 例 12/12 驗過。

### 上 prod
- Curated release **PR #4849**（spam-only，cherry-pick #4838/#4843/#4844/#4846 到 master，排除七日書）已 merge + 部署成功（含 report_reason migration）。修了卡死的 `report-telegram-alert-production` stack（刪除重建）→ Telegram 通知鏈接通。
- 三層 alert prod release（#4852）已 merge。

### 留言層校門檻的關鍵發現
- **線上自動折疊門檻＝0.94**（`feature_flag.spam_detection.value`，非先前假設的 0.8）。
- 7 天約 1,620 則打分，≥0.9 僅 24 則。**即使 0.94，純看分數精準度只 ~60%**（中文創作、短回覆、道謝、評論被誤判）。
- **分數與帳號年齡都無法單獨分辨**（escort 帳號已 818 天 / 883 篇）。
- **零誤殺分辨真垃圾的＝複合閘**（score + 聯絡方式 + 招攬詞）。→ 這就是 A 層的設計依據。

---

## 2. 文章層（article）— 本期主線

### 起點：conformal abstention serving
- conformal 三件套（decider / serving_decide / reasons）+ app.py 接線已在 `spam-detection-serverless` main（PR #2）。對外仍回 `score`（向後相容），新增 `decision/reason`。

### VPC 執行入口（CodeBuild-in-VPC）
- read-replica 在 VPC 內、GitHub runner 連不到 → 建 `infra/vpc-runner/`（CodeBuild 掛 prod VPC 私網，讀 SSM DSN、寫 S3，JOB=ring/acceptance/l1/heldout）。
- 實機修三個坑：subnet JSON 組法、**排除 IGW 公有子網**（CodeBuild ENI 無 public IP，只用兩個 NAT 私網）、salt 參數真名。

### 診斷：模型有「華語內容偏差」
- 對乾淨 held-out（老帳號 id>截點）acceptance：**舊模型把華語政治評論/學術/日記/時評以 0.98–1.0 高信度誤判 spam**，conformal 的 review abstention 救不了高信度誤判。
- 量化（subagent 確認的真 ham/spam）：**舊模型真誤殺 24%**（@0.5）。
- 根因：訓練標籤被 **「作者被限制」代理標籤**污染——一個作者被限制，名下全部文章被標 spam，掃進大量合法政治/創作/新聞。

### 規模化標籤矯正（不用 API/Bedrock，用 Claude subagent）
- 4 輪 Sonnet subagent（6×100 + 8×125 + 10×130 + 16×130）= **4,980 筆矯正標籤：2,649 hard-negative ham（誤標的合法內容）+ 2,331 spam**。
- 證實「乾淨外觀」spam 標籤 bucket（~39k）約 **55% 是誤標合法內容**。

### 重訓 v1 + 驗收
- 校正訓練集（套用矯正 + hard-negative ham 過採樣 ×5）→ GPU 訓練（g4dn.xlarge，~$1，79 分鐘）。
- **完整 acceptance（625 篇 subagent 確認的乾淨 held-out）：真誤殺 24.0% → 1.6%（@0.5），spam recall 97% → 92%。**
- 對照組：純圖片試金石 12 篇舊模型全誤殺 → v1 修好（剩 2 篇是偽裝 SEO，模型判對）。

### 部署上 prod
- 重建 conformal calib（v1 在 held-out 的分數，LOO eps=0.02 ham 誤block 1.9%）。
- staging 部署 + live smoke-test 通過 → **prod 促進**（`deploy-prod-conformal.yml`，confirm 防呆 + rollback-on-failure）成功，更新 prod stack `article-spam-model-v20251229`。
- **live prod smoke-test 通過**：舊模型誤殺的政治/學術/日記/時評 → v1 全 0.03–0.33；spam → 0.96+。

### 上線生效範圍
- `feature_flag.spam_detection` = on / **0.94**。v1 配 0.94：合法內容 0.03–0.33 < 0.94 → **不再被排除**；spam ≥ 0.94 → 照樣排除。
- **新文章即時生效。** 舊的、之前被埋沒的合法文章保留舊分數（無 re-score job）——本期決定**不回溯重打分**。

---

## 3. 軸二：訓練資料留存（為持續改善鋪路）

- **L1**（被動增量抽取）：SQL 寫好（CW 未推翻處置=spam、被推翻=hard-negative ham），匯出 job code 就緒。
- **L2**（主動快照防硬刪）：matters-server #4846 emit（HMAC 去識別）+ scaffold worker（SQS→S3）。AWS 基建（S3 桶 365 天 lifecycle、SQS+DLQ、Lambda worker、SSM）已建 + smoke-test 過。
- **L3**（標籤品質）：組裝器 dedup + ham 覆蓋 spam + label_weight。
- 保留期＝**1 年（365 天）**（法務拍板）。

---

## 4. 踩過的雷（完整清單，細節見研究報告）

1. **payload 格式假象**：送 JSON `{"text":...}` 給文章 endpoint → 恆回 1.0 → 假「100% 誤殺」。改 raw body。
2. **proxy 污染假象**：「老帳號 ham」proxy 約 37% 其實是 spam → 裸誤殺 36% 是假象，subagent 去污染後真值 1.6%。
3. **金融偽裝**：貸款/券商/討債帳號發的「防詐科普」+ 結尾導流 = SEO spam，被誤標 ham（模型反而抗拒、判對）。
4. **純平衡會洗鈍模型**（POC #2）→ 改「修標籤」才對。
5. 部署坑：IGW 公有子網無 egress、SageMaker 輸出 gzip tar 但 Dockerfile 用 `tar -xf`、SageMaker role 無 spam-detection-model 讀權限、replica 昂貴查詢被 recovery 取消、卡死的 CFN stack。

---

## 5. 結案待辦（移交清單）

**已完成上線**：留言層全鏈（偵測+bot+三層 alert）、文章層 v1+conformal、帳號 ring 打掃、軸二 L1-L3 程式 + L2 基建。

**待 ops / 後續**：
- ⚠️ HF token `hf_dBRf…` 輪替（git 歷史洩漏，使用者處理中）。
- ops 決定 `MATTERS_COMMENT_SPAM_AUTO_COLLAPSE`（留言自動折疊；本期決定**先不開**，維持 notify-only）。
- ops 開 `MATTERS_COMMENT_SPAM_ALERT=true` 啟用三層 telegram 分流（觀察 A 層乾淨後再考慮升級自動處置）。
- 軸一 C：matters-server 讀文章 conformal `decision` 接 community-watch 人工 review 佇列（下節）。
- 觀察 prod spamScore 分布，團隊微調門檻。

**可選**：v2 polish（金融偽裝再標）、master↔develop git 併回去重。
