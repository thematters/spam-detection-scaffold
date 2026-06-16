# Matters 平台留言濫用治理體系：2026 年度實施技術報告

**報告類型**：實施技術報告（Implementation Technical Report）
**涵蓋期間**：2026 年 2 月 – 6 月（資料截止 2026-06-16）
**涵蓋系統**：`matters-server`、`spam-detection-scaffold`、`spam-detection-serverless`、`matters-coastguard-bot`
**編纂方法**：本報告之事件、日期與技術細節，逐一回溯自四個版本庫之 Pull Request 與 commit 變動紀錄；文中所有 `#NNNN` 為 `matters-server` 之 PR 編號，`PR #N`（標明版本庫者）為對應 repo 之 PR，`commit <hash>` 為直接提交。完整對照見附錄 A。

---

## 摘要

本報告記錄 Matters 平台於 2026 年上半年，針對留言（comment）與文章（article）濫用內容所建立之完整治理體系。該體系由三個協同層次構成：(1) **群眾參與治理層**——「守望相助」（Community Watch）社群協作審查機制，於 2026 年 5 月以九個階段、約一週內完成建置並上線生產環境；(2) **自動化偵測層**——專用留言垃圾偵測模型，及以其為基礎、運行於排程之「海巡」自動執法機器人；(3) **處置層**——伺服器端三層告警與自動摺疊機制，以及帳號層詐騙集團（ring）偵測。

報告同時記錄文章層偵測模型之重大矯正：原模型因訓練標籤之「作者連坐」偏差，將華語政治評論、學術考據與個人創作以高信度誤判為垃圾；經規模化標籤矯正與重訓後，真實誤殺率由 24.0% 降至 1.6%，而垃圾召回率維持於約 92%。此外亦建立三層訓練資料留存體系（Axis-2 L1/L2/L3），確保處置決策之訓練訊號不因內容刪除而流失，並完成支援上述工作之基礎設施（GitHub OIDC 部署、VPC 內運算入口）。

全體系之核心設計原則為：**處置上限為「降低能見度」而非刪除（「不刪除，只是不再被看見」），不確定者交付人工，且全程可逆、可申訴、可審計。**

---

## 1. 緒論

### 1.1 背景與問題

Matters 為一抗審查之中文書寫平台，其價值核心在於保護易遭消音之內容。然平台同時承受持續之濫用內容壓力，型態涵蓋色情招攬（外送茶、約妹）、博弈廣告、詐騙、貸款／討債招攬，以及偽裝為知識科普之搜尋引擎最佳化（SEO）內容農場。此類內容於 2026 年呈現兩項結構特徵：(1) **高度模板化**——同一文本經少量變體跨多帳號重複張貼；(2) **群集化（ring）**——以「開新帳號→張貼重複文本」之模式規模化散布。

濫用內容之治理在此類平台上存在一項內生張力：抑制垃圾與抑制異議在技術上共用同一機制（一個分數加一個門檻），在價值上卻彼此對立。本報告所載之工程實作，即在此張力下尋求兼顧「有效攔截垃圾」與「不誤傷正當表達」之解。

### 1.2 範圍

本報告聚焦於 2026 年上半年之治理體系建置，主軸為**留言濫用處置之全過程**，並涵蓋與之共構之文章層模型矯正、訓練資料留存與基礎設施。2024–2025 年之前期工作（文章垃圾偵測與推薦排除 #4117、`setSpamStatus` 留言／動態垃圾狀態 #4703 等）僅作為脈絡簡述，不展開。

### 1.3 治理體系總覽

| 層次 | 組件 | 所在版本庫 | 功能 |
|---|---|---|---|
| 群眾參與 | 守望相助 Community Watch | `matters-server` | 社群成員協作審查、移除留言；含稽核、透明頁、申訴、覆核 |
| 自動偵測 | 留言專用模型 + 海巡 bot | `spam-detection-scaffold` + `matters-coastguard-bot` | 模型打分；bot 排程巡查、檢舉、打掃 |
| 自動偵測 | 文章模型 + conformal | `spam-detection-serverless` | 文章打分；conformal 決策（allow/block/review） |
| 處置 | 自動摺疊、三層告警、ring gate | `matters-server` + `matters-coastguard-bot` | 依分數與複合訊號分流處置 |
| 資料 | 訓練樣本留存 L1/L2/L3 | `matters-server` + `spam-detection-scaffold` | 確保訓練訊號不因刪除而流失 |
| 基礎設施 | OIDC 部署、VPC runner | 各 repo | 無金鑰部署；解鎖需連 replica 之批次工作 |

---

## 2. 群眾參與治理層：守望相助（Community Watch）

守望相助為本治理體系之制度基石，係將部分內容審查權限以受控方式委派予經授權之社群成員，並以稽核表、透明頁、申訴與覆核機制確保正當程序。其建置於 2026 年 5 月 10 日至 17 日間，以九個階段循序完成並上線生產，為一典型之「先建稽核與透明、再開處置權」之漸進式治理部署。

### 2.1 階段化建置（2026-05）

| 日期 | PR | 階段與內容 |
|---|---|---|
| 05-11 | #4762 | **階段一：功能旗標**。新增 `communityWatch` 使用者功能旗標；遷移 script 對 `user_feature_flag` 去重並加上 `unique(user_id,type)` 約束。 |
| 05-10 | #4763 | **階段二：移除 API**。新增 `community_watch_action` 稽核表（含原始內容 7 日留存中介資料）；`communityWatchRemoveComment` mutation，受旗標控制，限文章／動態留言；接受之留言標記為 `banned`、寫入證據、通知作者、清除快取。 |
| 05-11 | #4764 | **階段三：留言上揭露稽核行動**。`Comment.communityWatchAction` 對外揭露作用中之稽核行動（公開 UUID、理由、時間），供前端佔位連結。 |
| 05-11 | #4765／#4769 | **階段四：公開稽核查詢**。公開之稽核行動列表／明細查詢；內部 id 對映為公開 GraphQL ID；僅揭露行為者顯示名稱。為透明頁 `community-watch.matters.town` 之資料源。 |
| 05-11 | #4771 | **階段五：審查人員覆核行動**。append-only `community_watch_review_event` 表；管理員專用之申訴／覆核狀態變更、留言還原、原始內容清除等 mutation。 |
| 05-14 | #4786／#4790 | **階段六：徽章**。新增 `community_watch` 使用者徽章型別；管理員切換旗標時同步徽章；對既有旗標持有者冪等回填。 |
| 05-14 | #4791 | **階段七：覆核結果通知**。新增官方通知型別（還原、推翻處置、成員存取變更）；於還原時通知作者、於推翻時通知成員。 |
| 05-16 | #4797 | **階段八：內容留存對齊**。停止對新建之稽核原始內容指定 7 日到期；`content_expires_at` 改為可空。此一變更後續與 L2 訓練資料之法務審查相關。 |
| 05-17 | #4772 | **階段九：上線生產**。批次 `develop→master`，生產環境 GraphQL 正式揭露 `communityWatchActions`／`communityWatchAction`。 |

### 2.2 上線後修正與整合

守望相助上線後，由 staging 與生產環境之實測驅動了一系列修正，反映「讀寫一致性」與「使用者體驗」於透明審查場景之特殊要求：

- **05-13 #4775 讀寫一致性**：透明頁於還原後立即讀取仍見舊狀態，因稽核列讀自唯讀副本；改為讀取主庫以滿足 read-after-write。
- **05-13 #4779 留言列佔位**：當被移除留言為文章唯一留言時，文章頁留言區整段消失；改為在留言列中保留「已移除但具作用中稽核行動」之佔位。
- **05-18 #4801／#4802 還原狀態與來源連結**：還原時正確標記申訴已解決並寫入分離之覆核事件；新增可空之 `sourceUrl`。
- **05-28 #4822／#4824 OSS 通報整合**：守望相助移除行為改為同時建立常規 `report` 列，於 `OSS.reports` 以 `filter.source` 統一檢視；新增隱私保全之 `contentHash`（同內容群集而不重新散布垃圾文本）與批次 `archiveUsers`（≤50）。
- **06-02 #4828**：批次發布上述 OSS／通報整合至生產。

> **設計觀察**：守望相助之建置順序——先稽核表與透明頁、再覆核與申訴、最後才大規模整合 OSS 後台——體現「處置權之授予須以可問責性為前提」之治理原則。`contentHash`（#4822）尤值一提：它使後台得以將同一垃圾文本之多次出現群集呈現，卻不在後台重新散布該文本，為隱私與營運效率之折衷。

---

## 3. 通報與告警基礎設施

留言濫用之即時可見性，依賴於 2026 年 6 月初重構之告警骨幹。此骨幹後續為垃圾偵測之三層告警所重用。

- **06-04 #4830 即時 Telegram 告警**：對新通報（`submitReport` 或 `communityWatchRemoveComment`）發出近即時 Telegram 告警。架構上將所有 Telegram 機密與呼叫**移出 API 執行階段**，改為：mutation 持久化後將 `ReportAlertRequested` 入列 SQS → `reportTelegramAlert` Lambda worker → Redis 24 小時去重 → Telegram 發送／編輯。守望相助事件以被檢舉作者為鍵，使單一垃圾散布者集中於一則告警。
- **06-04 #4837**：無程式碼之觸發 PR，用於以版本庫密鑰啟動 develop 部署。

> 此重構之意義在於**將機密與外部副作用自主 API 隔離至專用 worker**，為日後垃圾偵測告警（§5.3）提供可重用之 SQS→Telegram 通道。

---

## 4. 自動化偵測層（一）：留言專用模型

### 4.1 模型之必要性

生產環境原以「文章／短內容共用模型」為留言打分。然 `spam-detection-scaffold` 之留言資料集分析（PR #4，2026-06-04 合併）顯示：留言垃圾高度模板化（約 50 個獨特模板家族），共用之文章模型於未見模板上召回率僅約 0.68。

### 4.2 模型設計（`spam-detection-scaffold`）

- **commit `49d656b`（06-03）**：留言層標籤收集與基線打分；正樣本取自守望相助已移除之留言，以 `contentHash` 為模板家族。
- **commit `b18e84c`（06-03）**：核心模型——e5-small 詞嵌入 + 邏輯迴歸分類頭 + serving。以「留一家族交叉驗證」（leave-one-family-out CV）量得未見模板召回率 0.880，遠優於共用模型之 0.678；偽陽率約 0.33%；CPU 訓練零成本。
- **commit `bc84eef`（06-03）**：以分組 K-fold 之 out-of-fold 分數校準門檻。
- **PR #4（06-04 合併）**：上述工作併入主幹。
- **commit `42235e4`（06-03）**：部署改採 GitHub OIDC（無儲存 AWS 金鑰），確立日後各 repo 重用之 `infra/oidc` 模式。

### 4.3 伺服器端接線（`matters-server`）

- **06-04 #4838 專用留言模型端點**：留言打分改路由至專用留言模型（`MATTERS_COMMENT_SPAM_DETECTION_API_URL`），未設定時回退至共用短內容模型 → 零停機切換。召回率 0.68→0.88。打分結果存入 `spamScore`。

---

## 5. 自動化偵測層（二）：海巡 bot 與處置層

「海巡」（coastguard）bot 為一獨立版本庫 `matters-coastguard-bot`，採主幹開發（trunk-based，無 PR），以 GitHub Actions 每六小時排程運行。其全部歷史皆於 2026 年（首次提交 06-03）。bot 之設計哲學為「安全預設」：預設 staging 環境、預設 dry-run、移除功能預設關閉、具總開關與多重煞車。

### 5.1 階段化推進

bot 之推進透過版本庫變數（repo Variables）驅動，使階段推進無須改碼：

| 日期 | commit | 內容 |
|---|---|---|
| 06-03 | `fcbf1b8` | **初始建置**。detect→tier→report/remove；複用留言模型確保 bot 行動與模型分數不分歧；佔位門檻 0.99 移除／0.95 檢舉。 |
| 06-03 | `07bd7a9` | **資料校準門檻**。以 group-K-fold OOF 取代佔位值：Tier-2 移除 = 0.80（召回 0.64，過殺≈0）、Tier-1 檢舉 = 0.55（召回 0.94，過殺≈0.5%）。 |
| 06-04 | `a8b9c54` | **以版本庫變數驅動之階段推進 + RUNBOOK**。階段 0→1→2，排程預設為生產觀察（dry-run）。 |
| 06-06 | `9c0dd4a` | **Tier-1 調至 0.80（生產觀察）**。階段 0 於 405 則實際留言僅標出 3 則 Tier-1 候選（0.59–0.64），全為偽陽（line/telegram 之善意提及、短語）；0.55 帶於線上過於雜訊 → 提高檢舉門檻至高精度之 0.80。 |
| 06-06 | `79a4e8b` | **Cloudflare 強化**。`server.matters.town` 位於 Cloudflare 後；匿名重度搜尋遭 403。修正：dry-run 亦先登入使請求帶 token；以 `cloudscraper` 建立 session。 |
| 06-13 | `fd67b3f` | **修 dispatch dry-run 陷阱**。`type:boolean` 預設值會於無輸入之手動觸發時覆寫版本庫變數，使手動觸發悄然回退 dry-run；改為 `choice [inherit\|true\|false] default inherit`。 |
| 06-13 | `223c56f` | **報告階段：將移除層級降級為檢舉**。兩個耦合陷阱使階段一對最嚴重垃圾形同無作用：(1) `TIER1_REPORT_THRESHOLD` 變數設為 0.80（=Tier-2），檢舉帶 `[0.80,0.80)` 為空 → 永不檢舉；重設為 0.55。(2) 達移除門檻但移除關閉者被**跳過**而非檢舉；改為降級為檢舉。 |
| 06-13 | `013ce03` | **採用 DB 合法之檢舉理由 `illegal_advertising`**（詳 §5.4）。 |
| 06-15 | `8b7adf2` | **Ring gate：以守望相助證據自動打掃（軸一 D）**（詳 §5.2）。 |
| 06-15 | `112d041` | **Ring：近似比對 + OpenCC 繁簡折疊**。精確雜湊漏失繁簡對與小幅編輯；改為正規化（遮罩 url/@/數字、t2s 折疊、去拉丁聯絡碼）後字元 3-gram Jaccard ≥ 0.8。 |
| 06-15 | `45261d2` | **回退文章層 ring 關鍵字**（HEAD）。律師／翻譯／貸款／人權等屬文章層 ring（軸一 D 文章偵測→帳號凍結），與留言垃圾不同；海巡 bot 僅司留言，故回退。 |

### 5.2 Ring gate（軸一 D 之留言側）

`bot/ring.py`（commit `8b7adf2` 引入）將守望相助之「三人檢舉即摺疊」規則鏡射至自動打掃：自確認之守望相助移除紀錄（排除已被推翻者）建立模板家族索引；若候選之家族已有 ≥3 筆確認之類似移除，即視為已知垃圾集團，**不論模型分數**逕予打掃。其動機在於 ring（新帳號 + 重複模板）正是逐則打分所漏者——例如「高雄翻譯社」模型僅給 0.09、「live173」0.12。near-dup 比對經 OpenCC 繁簡折疊後，繁簡變體 Jaccard 達 0.81–0.90，善意內容為 0。

### 5.3 最終執法架構（三層）

bot 每次運行之管線：安全前置檢查 → 公開掃描候選 → 留言模型打分 → 於精度閘、ring gate、流量上限下決定層級 → 行動（除非 dry-run）→ 持久化狀態。三層由高至低優先：

1. **Ring 命中 → 打掃**（`ring_count ≥ 3`，凌駕分數）。
2. **Tier-2 移除**（`score ≥ 0.80`）：`communityWatchRemoveComment`，伺服器端自動同步一筆通報。需 `ENABLE_REMOVE`。
3. **Tier-1 檢舉**（`score ≥ 0.55`，工作流變數覆寫為 0.80）：`submitReport`；三名不同檢舉者於伺服器端自動摺疊。
   - **報告降級**：合於移除但 `ENABLE_REMOVE` 關閉時，降級為檢舉而非跳過。

**護欄**（`bot/config.py`）：`DRY_RUN`（預設真）、`KILL_SWITCH`、`ENABLE_REMOVE`（預設假）、**reversed-rate 煞車**（近 100 筆守望相助行動之被推翻率 >0.10 即本次拒絕行動，表示其移除正被推翻、模型／門檻漂移）、流量上限（每次 `MAX_ACTIONS=20`、`MAX_CANDIDATES=500`）、預設環境 staging 防誤觸生產。

### 5.4 伺服器端處置與一次「實彈」修正

- **06-14 #4843 自動摺疊（env 旗標後）**：首個基於 `spamScore` 之伺服器端處置。`MATTERS_COMMENT_SPAM_AUTO_COLLAPSE=true` 時，對作用中且分數 ≥ 可調系統門檻之留言予以**摺疊（非刪除——「不刪除，只是不再被看見」）**；略過 `bypassSpamDetection` 白名單作者；預設關閉（觀察模式）；可逆。
- **06-13 #4844 報告理由約束修正**：遷移 script 將 `community_watch_porn_ad`／`community_watch_spam_ad` 加入 `report_reason_check` 約束。此 bug **由海巡 bot 之 Tier-1 檢舉於生產實彈觸發**——每筆 `community_watch_porn_ad` 插入皆遭 DB 約束拒絕（GraphQL enum 與 DB 約束漂移）。此為外部 bot 執法臂首次於生產留下印記。

### 5.5 三層複合告警（notify-only）

- **06-16 #4851 三層告警至管理員 Telegram**：於 `detectSpam` 內置複合三層閘，經既有 `report-alert` SQS→`reportTelegramAlert` worker（新來源 `spam_detection`）通報。**動機**：於 `spamScore ≥ 0.94`，生產精度僅約 60%——色情廣告與創作得分相同，帳號年齡亦無法區分。
  - **A 層（auto）**：`score≥門檻 ∧ 含聯絡管道 ∧ 含招攬詞`——對真實生產資料零偽陽。
  - **B 層（ring）**：作者 30 日內 ≥3 則近似（字元 3-gram Jaccard）。
  - **C 層（review）**：高分但非 A/B → 人工確認，**永不自動處置**。
  - 純邏輯於 `commentSpamSignals.ts`（regex + Jaccard，附測試）；受 `MATTERS_COMMENT_SPAM_ALERT` 控制，預設關；notify-only，永不隱藏。
- **06-16 #4852 三層告警上生產**（curated cherry-pick，排除七日書）。

---

## 6. 文章層與模型偏差矯正

留言治理之同時，文章層偵測模型暴露出一項嚴重之分類偏差，其矯正構成本期另一主線。完整之分析、案例與教訓見另冊《研究報告》；本節僅記錄工程實作之編年。

### 6.1 Conformal abstention serving（`spam-detection-serverless`）

- **commit `a4abd49`（06-13）**：conformal abstention serving 層（自 6/5 工作還原）。類別條件式 conformal decider：儲存每類校準分數陣列，推論時以二分搜尋求 p-value（O(log n)）；保證 P(正當內容被封鎖 | 真 ham) ≤ ε，與類別先驗無關；輸出 allow/block/review + 處置理由。**同一 commit 並自 notebook 移除一個硬編碼 HF token**（該 token 仍存於 git 歷史且仍有效 → 須另行輪替；本報告撰寫期間已完成 git 歷史清除，見 §10）。
- **PR #2（06-13 合併）**：conformal 層併入主幹。將正當內容誤殺由約 5–12% 降至約 1%，灰區（約 1–4%）轉人工，每筆處置附理由（對應 DSA 第 17 條之處置理由聲明）。向後相容：仍回 `score`，新增 `decision`/`reason`/`p_spam`/`p_ham`。LOO：ε=0.02 → ham 誤封 1.8%、召回 93%。
- **PR #3 runbook（06-13）**：staging 部署 runbook 與 shadow-eval 證據；誠實註記「conformal 之 mis-kill ≤ ε」為期望／有限樣本性質，非硬保證。
- **PR #5 OIDC staging 部署（06-13）**：本 repo 首個 CI；建置至**隔離** stack `article-spam-conformal-staging`，硬性拒絕生產 stack。

### 6.2 規模化標籤矯正（`spam-detection-scaffold` PR #10）

- **PR #10 LLM-judge 標註引擎（06-15）**：矯正華語偏差之核心。文章模型以 0.98–1.0 高信度誤判華語政治／學術／日記／金融書寫為垃圾（conformal 無法挽救高信度誤判），矯正須以修正後標籤重訓，尤需華語 hard-negative ham。`llm_label_articles.py`（抗偏差判準、看結構不看主題、低信度→review）+ `validate_labeler.py`（對 136 筆 gold 之強制 gate）。
- 因 Bedrock 之 Anthropic 模型存取未開通，實際標註改採平台同信任邊界內之 Claude subagent 平行執行（詳見《研究報告》§6）。

### 6.3 重訓、驗收與部署

- **驗收基礎設施**：`eval/staging_conformal_accept.py` 經多次精修——改送 RAW body（非 JSON）、`--source replica` 撈訓練截點後之乾淨 held-out、傾印誤殺個案、`--dump` 模式（PR #11，06-16）使尚未部署至端點之重訓模型亦可跑完整驗收（VPC 撈 held-out → 本地打分）。
- **重訓 v1 與部署**（`spam-detection-serverless` commit `3117756`/`bb18dc7`、PR #6，06-16）：v1（label-fix 重訓）conformal calib 部署至 staging 並實機驗證。calib = v1 於 625 筆 held-out（subagent 確認之真 ham/spam）之分數；LOO ε=0.02 ham 誤封 1.9%／召回 93%；完整驗收真誤殺 **舊 24.0% → v1 1.6%（@0.5）**；實機 smoke：舊模型誤殺之政治／學術／日記／時評 → v1 全 allow/review，真垃圾 → block。新增 `deploy-prod-conformal.yml`（確認防呆 + 失敗回滾）促進至生產。

---

## 7. 訓練資料留存體系（Axis-2 L1/L2/L3）

為使處置決策之訓練訊號不因內容刪除而流失，建立三層留存體系。法務於 2026-06-14 拍板留存期為一年（S3 lifecycle 365 天，scaffold commit `94b5bba`）。

- **L1 被動增量抽取**（scaffold commit `3ecccf3`/`b09a8b8`）：每日自唯讀副本抽取（受限作者／is_spam／spamScore≥門檻為 spam；被推翻／還原之守望相助行動為 hard-negative ham）→ S3，需於 VPC 內執行。
- **L2 主動快照**（`matters-server` #4846 + scaffold commit `4603589`）：於審查邊界將去識別之標註樣本入列 SQS（`enqueueSpamSample`，盡力而為、永不拋例外、未設定時 no-op）；留言／作者 id 於發出時即以 HMAC-SHA256(salt) 去識別，佇列中無原始 id。接線於 `communityWatchRemoveComment`（確認垃圾）與 `clearCommunityWatchOriginalContent`（清空前捕捉；`reversed` ⇒ ham）。AWS 基建（S3、SQS、Lambda worker）於 06-14 建置並 smoke-test。
- **L3 標籤品質與組裝**（scaffold commit `460e3db` + PR #10）：以 `comment_hash` 去重、標籤優先序（ham 覆蓋 spam＝人工推翻優先）、`label_weight`（人工 2.0／受限-管理 1.0／僅模型 0.5）。標註引擎（PR #10）為 L3 之運營核心，餵入了 label-fix v1 重訓。

---

## 8. 基礎設施與發布工程

- **GitHub OIDC 部署**：scaffold `deploy-comment-model.yml`（commit `a6f7d11`/`42235e4`，最初之 OIDC 模式）；serverless `deploy-staging-conformal.yml`（PR #5）與 `deploy-prod-conformal.yml`（PR #6）。全程無儲存 AWS 金鑰。
- **VPC CodeBuild 運算入口**（scaffold PR #7/#8/#9）：GitHub 託管 runner 無法連抵位於 VPC 內之生產唯讀副本，故建單一 CodeBuild-in-VPC 專案，以單一入口服務三項需連 replica 之工作（ring SQL／conformal 驗收／L1 匯出）。`setup.sh` 為冪等之管理員執行 script。實機修正包含：subnet JSON 陣列組法、**排除 IGW 公有子網**（CodeBuild ENI 無公網 IP，僅用 NAT 私網）、SSM salt 參數真名 `MATTERS_SPAM_SAMPLE_HASH_SALT`。
- **curated 發布與 git 衛生**：
  - **06-15 #4849 spam-only 發布**：自 `develop` curated cherry-pick 至 `master`，**排除七日書**（#4841/#4842），使垃圾防治不受其排程阻塞（使用者 2026-06-15 之決策，B 方案）。涵蓋 #4838/#4843/#4844/#4846 + 測試 + `codecov.yml`。
  - **06-16 #4850 back-merge master→develop**：使 master 為 develop 之祖先，避免後續七日書批次發布與 cherry-pick 之 spam commit 衝突。純 git 衛生。

---

## 9. 量測與驗證結果彙總

| 指標 | 數值 | 來源 |
|---|---|---|
| 留言模型未見模板召回率 | 0.88（共用模型 0.68） | scaffold leave-one-family-out CV |
| 留言模型偽陽率 | ≈0.33% | 同上 |
| 文章模型真誤殺率（@0.5） | 舊 24.0% → v1 **1.6%** | 625 筆 subagent 確認之乾淨 held-out |
| 文章模型垃圾召回率 | 97% → 92% | 同上 |
| Conformal LOO（ε=0.02） | ham 誤封 1.9%、召回 93% | v1 calib |
| 生產自動摺疊／排除門檻 | 0.94（`feature_flag.spam_detection.value`） | 線上設定 |
| 純分數於 0.94 之精度 | ≈60% | 七日約 1,620 則打分之人工檢視 |
| 複合 A 層閘偽陽 | 0 | 真實高分集實測 |

---

## 10. 現狀、待辦與風險

**已上線生產**：守望相助全機制；留言專用模型 + 海巡 bot 三層執法（含 ring gate Phase 2）；三層告警（notify-only，#4852）；文章 v1 + conformal；訓練留存 L1–L3 程式與 L2 基建。文章誤殺修復對**新內容**即時生效。

**本期決策（不執行）**：不回溯重打分舊文章；不啟用留言自動摺疊（`MATTERS_COMMENT_SPAM_AUTO_COLLAPSE` 維持關，notify-only）。

**安全債——已處理**：硬編碼之 HF token 原僅自工作樹移除而仍存於 `spam-detection-serverless` git 歷史；本報告撰寫期間已以 `git filter-repo` 重寫全歷史並 force-push（main/develop token commit 歸零，備份 bundle 留存）。惟**撤銷該 token（HF 帳號端動作）仍為必要之真正修復**，且協作者須 re-clone。

**待 ops／後續**：
- 軸一 C：`matters-server` 讀取文章 conformal `decision` → 接守望相助人工 review 佇列（roadmap 列為待端點回傳 `decision` 後進行）。
- 觀察生產 `spamScore` 分布，團隊微調門檻；觀察 A 層乾淨後再評估是否升級自動處置。
- 帳號層 ring 凍結（軸一 D 文章側）仍為候選階段，分級凍結與 shadow-first 尚未生產化。

---

## 附錄 A：完整變動對照

### A.1 `matters-server`（PR 編號）
守望相助：#4762, #4763, #4764, #4765, #4769, #4771, #4772（上線）, #4775, #4779, #4786, #4790, #4791, #4797, #4801, #4802, #4809, #4811, #4821（closed）, #4822, #4824, #4828。
留言模型與處置：#4838（專用模型）, #4843（自動摺疊）, #4851/#4852（三層告警）。
通報系統：#4830, #4837, #4844（理由約束）。
資料留存：#4846（L2 emit）。
發布／衛生：#4849（spam-only 上線）, #4850（back-merge）。

### A.2 `spam-detection-scaffold`（PR / commit）
PR #1/#2（bootstrap）；commit `49d656b`/`b18e84c`/`bc84eef` + PR #4（留言模型）；OIDC commit `a6f7d11`/`42235e4`；roadmap/L1 commit `3ecccf3`；L1 `b09a8b8`；L2 `4603589`；L3 `460e3db`；驗收 `c21c9bb`；PR #5/#6（ring D POC/v2）；PR #7/#8/#9（VPC runner）；PR #10（LLM 標註）；PR #11（held-out dump）；PR #12（初版兩份報告）。PR #3（codex hybrid policy）closed。

### A.3 `spam-detection-serverless`（PR / commit）
commit `a4abd49`（conformal serving）+ PR #2；PR #3（runbook）；PR #5（OIDC staging）；PR #4（access-key staging）closed；commit `3117756`/`bb18dc7` + PR #6（v1 部署 + 生產促進工作流）。

### A.4 `matters-coastguard-bot`（commit，主幹無 PR）
`fcbf1b8`（建置）, `07bd7a9`（校準門檻）, `a8b9c54`（階段化）, `9c0dd4a`（Tier-1→0.80）, `79a4e8b`（Cloudflare）, `fd67b3f`（dispatch 修正）, `223c56f`（報告降級）, `013ce03`（理由 `illegal_advertising`）, `8b7adf2`（ring gate）, `112d041`（near-dup + OpenCC）, `45261d2`（回退文章 ring 關鍵字）。
