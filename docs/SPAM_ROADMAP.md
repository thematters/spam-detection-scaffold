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

### D — 帳號層 ring 凍結（「開新帳號→貼重複文本」這類，內容打分擋不住）
**動機（2026-06-15 實證）**：純內容打分對這類濫用無能為力。對 5 個已知群集實測（`eval/ring_detect_poc.py`）：

| 群集 | 篇/作者/模板族 | 最大 ring | 內容模型 |
| --- | --- | --- | --- |
| 老灯闲聊 | 30 / 30 / 3 | 1 模板跨 **18** 帳號 | 部分純圖看不到 |
| 披着律师外衣的搅局者 | 30 / 29 / 2 | 1 模板跨 **28** 帳號 | 詐騙長文可 block |
| 海外中国人权律师联盟 | 30 / 27 / 3 | 1 模板跨 **25** 帳號 | ✅ block 0.996 |
| 高雄翻译社 | 30 / 21 / 10 | 1 模板跨 **11** 帳號 | ❌ allow 0.091（漏） |
| live173影音live秀 | 30 / 28 / 24 | 1 模板跨 4 帳號 | ❌ allow 0.117（漏） |

定義訊號不是文章語意，是**行為模式**：同一 `template_family` 被大量 throwaway 新帳號重複貼。ring 訊號抓得到內容模型漏掉的群集。

**三層設計（沿用 conformal「不確定就送人工」，升到帳號層）**：
1. **行為偵測（缺的那塊，材料現成）**：複用 `prepare_article_families.py` 的 `template_family` 指紋；
   group by family、count distinct author、過濾帳號新/低 karma → 跨帳號重複 ring。原型 `eval/ring_detect_poc.py`
   已用公開搜尋（唯讀、不碰 DB）證明可行；正式版改吃 read-replica / L1 匯出的近期文章。
2. **證據合流（內容模型當輔助不當唯一）**：ring 訊號 + 文章 conformal decision + 純圖旗標 三者合流。
3. **分級處置**：高信度 ring（帳號新 **且** 跨帳號重複 ≥M 篇 **且**（內容 block 或純圖重複））→ 凍結；
   其餘 → 人工佇列（重用軸一 C 的 Community Watch review，擴成可對帳號動作）。動作原語 = `user_restriction`/`archiveUser`。

**安全護欄（凍結高風險，硬性）**：① 雙鑰——≥2 獨立訊號才自動凍結，單訊號→人工；② 可逆+可申訴（限制非刪除、通知本人，DSA Art.17 一致）；
③ 老帳號（年齡/karma 過門檻）豁免自動凍結；④ 影子先行——上線前「只記錄不動作」跑一週看誤傷再開真實處置。

**閉環**：凍結即 `user_restriction`，而 L1 SQL 已把該訊號當正樣本 → 越凍越準。

**弱點/待解**：live173 模板變化多（24 族），純指紋 ring 偏弱 → 需近似比對（shingling/minhash）或補「帳號名亂碼」訊號；繁簡字要正規化（實測「披著…」繁體零結果、簡體「披着…」28 帳號）。

**依賴鏈**：軸一 B 驗收 → C（佇列接帳號動作）→ **D（ring 偵測 + 分級凍結）**。

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

### L2 — 主動快照（防硬刪除）— 見下方「快照儲存建議」— code ✅
- 處置事件當下寫一筆去識別化樣本，與 live 內容生命週期解耦。
- 發送端（matters-server PR feat/spam-training-sample-capture）：`common/notifications/spamSample.ts`
  `enqueueSpamSample`（best-effort SQS，emit 端就 HMAC 去識別 id，PII 不進佇列）。已 wire
  `communityWatchRemoveComment`（移除即存 spam）、`clearCommunityWatchOriginalContent`（清除前存；
  reversed→hard-negative ham）。env `MATTERS_AWS_SPAM_SAMPLE_QUEUE_URL` / `MATTERS_SPAM_SAMPLE_HASH_SALT`。
- 消費端（scaffold `workers/spam_sample_worker.py`）：SQS→S3 Lambda，date-partition JSONL，
  key 含 messageId 做冪等。離線單測過。
- ✅ AWS 基礎建設已建好並 smoke-test 通過（2026-06-14，本 session 代建）：
  - S3 桶 `matters-spam-training-samples`（ap-southeast-1，封鎖公開、lifecycle 365 天到期）
  - SQS `matters-spam-sample` + DLQ `matters-spam-sample-dlq`（5 次重試後進 DLQ）
  - Lambda `matters-spam-sample-worker`（role `matters-spam-sample-worker`，SQS 觸發 batch 10）
  - SSM：`/{prod,dev}/matters-server/MATTERS_AWS_SPAM_SAMPLE_QUEUE_URL` + `..._HASH_SALT`(SecureString) 已設
  - 驗證：丟測試訊息 → worker → S3 寫入成功（測試物件已清）
  - **就緒,只待 matters-server #4846 merge + 部署**,prod 才開始真的擷取。
- 後續觸發點（未 wire，L1 已涵蓋或次要）：`archiveUsers`/ban 批次、留言 auto-collapse(#4843)。

### L3 — 標籤品質 + 隱私治理 — 組裝 code ✅
- 組裝器 `scripts/assemble_training_set.py`（核心 `resolve()`，離線單測過）：合併 L1+L2，
  以 comment_hash 去重，**ham 覆蓋 spam**（人工推翻優先於舊 spam 標籤），同 label 取
  來源強度最高+最新；加 `label_weight`（人工確認=2.0 / 受限·管理員=1.0 / 純模型=0.5，壓低誤殺）。
- 去識別已在 L1/L2 來源端完成（HMAC id、無可聯繫個資）；組裝器只處理已去識別資料。
- 保留期：**1 年（365 天）**（法務 2026-06-14 拍板）。S3 訓練桶須設 lifecycle 365 天到期；存取限訓練用途。
- 閉環：conformal review 經人工裁決的結果 = 高價值人工標籤 → 回流 L2 → 組裝器以高 weight 納入。

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
- ✅ AWS 基礎建設（OIDC、staging stack、S3 桶、SQS、worker）全部本 session 代建完成。
- **step 1（read-replica 內網存取）VPC 資訊已查到**（2026-06-14）：
  - VPC `vpc-02362a4fe3806ffac`（prod）；分析用 replica `matters-prod-replica-analysis`（與線上隔離,適合抽取/驗收）
  - 子網 subnet-0b011dd1ca64fa0a1 / subnet-08074bc162cd5a4a3 / subnet-0415147ddf68a48f2；DB SG `sg-0aff7c791291d103d`(5432)
  - 連線字串在 SSM `/prod/matters-server/MATTERS_PG_READONLY_CONNECTION_STRING`
  - L1 匯出 + conformal 正式驗收須改成「VPC 內」執行（VPC Lambda 或 VPC runner）才連得到 replica；GitHub-hosted runner 不在 VPC。
- ✅ 法務：保留期 = 1 年（365 天，2026-06-14 拍板）。建桶時設 S3 lifecycle 365 天。
- HF token 輪替（獨立資安項，見專案記憶）。

## 進度追蹤
- [x] 軸一查證（serverless 無 CI；develop 已有 review 系統）
- [x] 軸二查證（archiveUser 行為；community_watch_action 欄位）
- [x] L1 SQL（本 repo `sql/extract_spam_training_incremental.sql`）
- [ ] 軸一 B：serverless staging 部署 workflow
- [ ] L1：增量匯出 job（GH Action cron + read-replica secret + S3）
- [ ] L2：matters-server 快照事件 + SQS→Lambda→S3
- [ ] 軸一 A / C：留言 endpoint conformal + server 讀 decision
- [x] 軸一 D 原型：ring 偵測 POC（`eval/ring_detect_poc.py`，5 群集實證）
- [ ] 軸一 D：正式 ring 偵測（read-replica/L1 資料）+ 分級凍結 + 影子先行
- [ ] L3：標籤品質規則 + 隱私政策數值
