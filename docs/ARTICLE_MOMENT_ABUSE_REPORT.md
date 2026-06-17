# 文章與動態濫用之偵測、處置與模型矯正：Matters 平台 2024–2026 技術沿革與分析

**報告類型**：實施技術報告與分析（Implementation & Analysis Report）
**主題範圍**：**文章（article）與動態（moment）** 之濫用內容偵測、處置與模型更新——有別於另冊之留言（comment）治理報告
**涵蓋期間**：2024 年 8 月 – 2026 年 6 月（資料截止 2026-06-17）
**涵蓋系統**：`matters-server`、`spam-detection-serverless`、`spam-detection-scaffold`
**編纂方法**：本報告之事件、日期與機制，逐一回溯自三個版本庫之 Pull Request 與 commit 變動紀錄。文中 `#NNNN` 為 `matters-server` PR 編號；`PR #N（repo）` 為對應 repo 之 PR；`commit <hash>` 為直接提交。完整對照見附錄。

---

## 摘要

本報告記錄 Matters 平台針對**文章**與**動態**兩類長／短內容之濫用治理，自 2024 年奠基至 2026 年模型矯正之完整技術沿革。與留言治理（社群協作審查為主）不同，文章層之治理自始即以**機器偵測 + 自動降低能見度**為核心：2024 年 8 月之奠基工作（#4117）即確立「不刪除，只是不再被看見」之原則——將高分文章排除於推薦、熱門、標籤與頻道等發現面，而不刪除其內容。

報告涵蓋三條主線：(1) **體系沿革**——文章 spam 偵測自 2024 年奠基、2025 年預設啟用與白名單／重試／IPFS gating、以及動態與 `spamStatus` 之納入，至 2026 年之專用端點與 OSS 分流；(2) **模型更新與分數重新評估**——文章模型因訓練標籤之「作者連坐」而高信度誤判華語政治、學術與創作內容，經規模化標籤矯正重訓後，真實誤殺率自 24.0% 降至 1.6%，召回率維持約 92%，並記錄分數重新評估之嚴謹方法論（代理污染之發現與去除）；(3) **針對難偵測之重複樣態文章的主動攔截**——對「開新帳號→張貼重複文本」此類純內容打分無法攔截之濫用，建立以行為模式（跨帳號模板重複）為訊號之帳號層 ring 偵測與分級凍結設計。

報告亦揭示一項當前缺口：**動態（moment）目前僅被打分並呈現於後台分流，尚無任何自動處置機制**——與文章之自動排除、留言之自動折疊形成對比。

---

## 1. 緒論

### 1.1 範圍與定位

本報告專論**文章與動態**之濫用處置。留言（comment）之治理——尤以「守望相助」群眾協作審查為核心——已於另冊報告詳述；本報告僅在涉及**共用基礎設施**（同一 `SpamDetector`、同一 `spam_detection` 門檻旗標、同一 `bypassSpamDetection` 白名單、同一 `setSpamStatus` mutation）時提及留言。

文章層治理之架構特徵，與留言層有本質差異：留言治理倚重社群協作之人工審查；文章治理自始即為**機器偵測驅動之自動能見度調節**。理解此差異，是理解本報告全部設計取捨之前提。

### 1.2 治理之根本原則

文章層治理之原則於 2024 年 8 月之奠基 PR（#4117）即已確立，並貫穿其後全部演進：

> **處置之上限為「降低能見度」而非刪除——「不刪除，只是不再被看見」。** 高分文章被排除於推薦、熱門、標籤與頻道等發現面，但其內容、頁面與既有連結均保留。

此原則對一個抗審查之中文書寫平台尤具意義：它使「抑制垃圾」之代價被限制在「降低觸及」而非「消滅內容」，從而為日後發現之模型偏差（誤判政治／學術內容）保留了可逆性——被誤判者失去的是能見度，而非存在。

---

## 2. 體系沿革：三個紀元

### 2.1 紀元一（2024）：奠基——文章 spam 偵測與發現面排除

| 日期 | PR／commit | 內容與動機 |
|---|---|---|
| 2024-08-06 | `a1438252e` | 於發佈／修訂時觸發 spam 偵測（餵入 #4117 之前導 commit）。 |
| 2024-08-09 | **#4117** | **奠基 PR**。為 `article` 表新增 `spam_score`(float) + `is_spam`(bool) 欄位（遷移 `20240806102534_alter_article_spam.js`）；建立 `SpamDetector` 類別（POST 文字 → 讀 `.score`）；接線 `MATTERS_SPAM_DETECTION_API_URL` 環境變數；新增 `spam_detection` 功能旗標（旗標之 `value` 即為門檻）；並**將 spam 排除於文章 feed**。治理原則「不刪除，只是不再被看見」源於此。 |
| 2024-08-12 | **#4122** | **OSS spam API**。新增 `setSpamStatus` mutation，對後台揭露 spam 狀態；植入功能旗標。 |
| 2024-08-14 | **#4129** | 修正 `excludeSpam` 之 WHERE 條件（排除述詞之邏輯 bug）。 |
| 2024-10-30 | **#4210** | 於 campaign 與搜尋情境**停用** spam 偵測。 |
| 2024-11-07 | **#4216** | 回退 #4210。 |
| 2024-11-15 | **#4221** | 再回退（即重新套用 #4210——campaign／搜尋之排除停用恢復）。 |

> **觀察**：#4210→#4216→#4221 之反覆，反映了「搜尋與活動情境是否該排除 spam」之早期權衡擺盪。最終立場（搜尋與 campaign **不**排除 spam）延續至今——即垃圾仍可被「主動搜尋」到，只是不被「演算法推送」。這是「降低能見度而非消滅」原則之細緻體現：排除作用於被動發現面（推薦／熱門／標籤／頻道），而非主動查找。

### 2.2 紀元二（2025）：預設啟用、白名單、韌性，與動態之納入

| 日期 | PR／commit | 內容與動機 |
|---|---|---|
| 2025-01-14 | **#4264** | **白名單**。新增 `bypassSpamDetection` 使用者功能旗標；白名單作者豁免排除（經 `user_feature_flag` 接入 `excludeSpam`）。 |
| 2025-01-14 | **#4266** | **預設啟用 spam 偵測**。將發現面預設切換為排除變體：`relatedArticlesExcludeSpam`、標籤 `articlesExcludeSpam`、推薦 `hottest`／`newest`。 |
| 2025-03-25 | **#4372** | **重試邏輯**：`SpamDetector.detect` 最多 3 次嘗試含退避，最終失敗時 Sentry 捕捉。提升偵測對端點抖動之韌性。 |
| 2025-04-01 | **#4379** | **IPFS 發佈以非 spam 為前提**：IPFS/IPNS 發佈僅於文章非 spam 時觸發（經 `setSpamStatus`→`runPostProcessing`）。即垃圾不獲永久去中心化存證。 |
| 2025-05-08 | **#4455** | spam 文章略過語言偵測（後處理優化）。 |
| 2025-06-22 | `aed2836e1` | 重構：`ArticleService` 拆出 `publicationService`，spam 偵測邏輯遷入。 |
| 2025-06-25 | **#4578** | 修正：文章為 spam 時頻道文章列表回空之 bug。 |
| 2025-07-16 | `463c58fbe` / `5d3217b50` | 新增 `isSpam` 方法集中判定；修正分數比較邏輯。 |
| 2025-07-18 | `9d7c79873` | 修正：spam 分數預設為 0（`spamScore ?? 0`）以正確評估。 |
| 2025-10-14 | `3a7cfda47` | **動態／留言建立後偵測 spam**：引入 `momentService.detectSpam`，使用**短內容**模型端點。 |
| 2025-10-15 | **#4703** | **`setSpamStatus` 擴及留言／動態**：mutation 依節點型別（Article／Comment／Moment）切換，寫入 `is_spam`。 |
| 2025-10-16 | **#4702** | **Comment／Moment 之 `spamStatus` GraphQL 欄位**。為 `comment` 與 `moment` 表新增 `spam_score`+`is_spam` 欄位（遷移 `20251014025100`）；新增 `MATTERS_SHORT_CONTENT_SPAM_DETECTION_API_URL` 環境變數。 |
| 2025-10-17 | **#4706** | **OSS 動態 API + 動態 spam 惰性觸發**：新增 `oss.moments` 查詢；**`moment.spamStatus` resolver 於讀取時、當 `spamScore` 為 null 才觸發 `momentService.detectSpam`**（惰性打分）。 |
| 2025-10-24 | **#4707／#4708** | **`readSpamStatus` 使用者功能旗標**：管控誰能於後台讀取文章 spam 狀態（遷移 `20251024063032`）；旗標熱發布。 |
| 2025-10-30 | **#4711** | 發布 v5.20.0（含 #4702–#4708 之 spamStatus／動態工作）。 |

> **觀察**：2025 年 10 月之動態納入（#4702/#4703/#4706）在**資料模型**上將動態與留言對齊（同樣的 `spam_score`/`is_spam` 欄位、共用短內容模型、統一 `setSpamStatus`），但在**處置**上止步於「打分 + 後台可見」——動態之打分甚至是**惰性的**（僅於後台讀取且分數為 null 時才觸發）。此一「資料對齊、處置未跟上」之狀態延續至今（見 §4）。

### 2.3 紀元三（2026）：模型更新、專用端點與後台分流

| 日期 | PR／commit | 內容與動機 |
|---|---|---|
| 2026-06-04 | **#4838** | **留言專用模型端點**（`MATTERS_COMMENT_SPAM_DETECTION_API_URL`，未設定時回退短內容模型）。雖為留言而設，卻確立了「每類內容專用端點」之模式——值得注意者：**動態至今仍用共用短內容模型，未比照拆分**。 |
| 2026-06-14 | **#4843** | 留言自動折疊（共用 `getSpamThreshold` + `bypassSpamDetection`）——文章排除之留言類比，共用門檻／白名單基礎設施。 |
| 2026-06-15 | **#4846** | L2 訓練樣本擷取（`enqueueSpamSample`→SQS，HMAC 去識別）於審查邊界。共用 spam 資料基礎設施。 |
| 2026-06-17 | **#4856** | **OSS spam 分流排序**。`ArticlesSort.mostSpam` 依 `spam_score` 降冪排列（`whereNotNull`）；新增 `OSSCommentsInput`／`OSSMomentsInput` 含 `mostSpam` 排序 + 7 日 `datetimeRange` 篩選。使管理員得以審視 spam 機率最高之文章／動態／留言列表——是**動態 spam 分數目前唯一之消費者**。 |

並行於 `matters-server` 之上述演進，文章偵測**模型本身**於 2026 年 6 月經歷重大更新（conformal serving、偏差矯正重訓 v1），詳見 §5–§6；針對難偵測重複樣態之**主動攔截**設計詳見 §7。

---

## 3. 文章層處置機制（現行程式碼）

本節依現行程式碼精確描述文章之打分與處置路徑。

### 3.1 偵測器 `SpamDetector`（`src/connectors/spamDetector.ts`）
以 `apiUrl` 建構；`detect(text)` 以 axios POST JSON `{ text }`，回傳 `response.data.score`（`number | null`）。重試最多 3 次、退避 `1000 × retries` ms，最終失敗記錄並 `Sentry.captureException` 後回 `null`。**僅消費 `.score`——不讀取 conformal 之 `decision` 欄位**；伺服器端之處置純為門檻判定。

### 3.2 打分路徑（`publicationService`，約 L519–650）
- `detectSpam(id)` → 載入最新版本（標題、客製摘要）+ 內容 → 委派 `_detectSpam`。
- `_detectSpam(...)` 組文字 = `標題 + "\n" + (摘要 + "\n")? + 內容`，呼叫 `SpamDetector(environment.spamDetectionApiUrl)`，**分數為真值時**寫入 `article.spam_score`。
- `isSpam(articleId)`：載入 `spamScore`／`isSpam`／`authorId`；`bypassSpam` 取自白名單旗標；`spamThreshold = getSpamThreshold() || 1`；`spamScore = _spamScore ?? 0`。回傳 **`_isSpam ?? (bypassSpam ? false : spamScore ≥ spamThreshold)`**——即**人工 `is_spam` 覆寫（來自 `setSpamStatus`）永遠優先**，否則走門檻比較，白名單作者永非 spam。

### 3.3 發佈時閘（`runPostProcessing`）
若 `isSpam` 未定，先打分比門檻；**若判 spam → 提早返回，略過頻道分類、語言偵測、IPFS 發佈**。非 spam 才執行上述任務。此為發佈時之處置閘。

### 3.4 門檻（`systemService.getSpamThreshold`）
讀 `feature_flag` 中 `name=spam_detection, flag=on` 之 `value`（Redis 快取）；旗標關閉時為 null，呼叫端預設 `|| 1`。

### 3.5 發現面排除（`excludeSpam` modifier，`src/common/utils/knex.ts`）
述詞：保留列若 作者 ∈ `bypassSpamDetection` 白名單 **或** `is_spam = false` **或**（`is_spam IS NULL` 且（`spam_score < 門檻` 或 `spam_score IS NULL`））；僅於門檻已設時生效。套用於：`articleService`（newest／列表，並有反向「顯示 spam」之 OSS 分支）、`tagService`（標籤文章）、`channelService`（頻道）。**於 `campaignService` 與 `searchService` 停用（註解掉）**——搜尋與活動不排除 spam（延續 #4210/#4221）。

> **小結**：文章之處置是**寫入時打分 + 讀取時 SQL 排除**之雙段機制，門檻單一可調，人工 `setSpamStatus` 永遠凌駕模型分數。這套機制成熟、自動、可逆——也正因如此，模型若有系統性偏差，其後果（誤殺）會被自動、靜默地施加於全發現面，這使 §5–§6 之偏差矯正成為必要。

---

## 4. 動態（moment）專章

動態之治理狀態，是本報告須特別指出之處：**其資料模型已與文章／留言對齊，但處置機制付之闕如。**

### 4.1 現行機制（`momentService`、`queries/moment/spamStatus.ts`）
- `momentService.detectSpam({id, content})` 使用 **`SpamDetector(environment.shortContentSpamDetectionApiUrl)`**——**共用短內容模型，無專用端點**（不同於留言自 #4838 起之專用端點）；POST 原始 `content`，分數為真值時寫入 `moment.spam_score`。
- **觸發為惰性、讀取時**：`moment.spamStatus` resolver 僅於 `spamScore` 為 null 時才觸發 `detectSpam`（OSS 讀取路徑，#4706）。

### 4.2 缺口：動態無自動處置
- **無任何動態 feed 套用 `excludeSpam`，無門檻比較，無自動隱藏。** 動態 spam 目前僅被**打分並呈現於後台**；人工 `setSpamStatus` 於動態寫入 `is_spam`，但**無任何機制據此自動調節動態之能見度**。
- #4856 之 `mostSpam` 後台排序，是動態 spam 分數**目前唯一之消費者**。

### 4.3 三類內容之架構對照

| 面向 | 文章 | 留言 | 動態 |
|---|---|---|---|
| 模型端點 | 專用長內容（`MATTERS_SPAM_DETECTION_API_URL`） | 專用（#4838，回退短內容） | **共用短內容**（未拆分） |
| 打分時機 | 發佈／修訂時（eager，寫入路徑） | 建立時 | **惰性，後台讀取時** |
| 自動處置 | **排除發現面**（推薦／熱門／標籤／頻道）+ 發佈時略過 IPFS／分類／語言 | **自動折疊**（#4843，較移除溫和） | **無**——僅打分 + 後台分流 |
| 人工處置 | `setSpamStatus`（覆寫分數） | `setSpamStatus` / 守望相助 | `setSpamStatus`（寫入但不自動消費） |
| 共用基礎設施 | `SpamDetector` + 重試／Sentry、`spam_score`/`is_spam` 欄位對、`getSpamThreshold` 單一門檻、`bypassSpamDetection` 白名單、統一 `setSpamStatus` |

> **建議（缺口）**：動態若需自動處置，最低成本路徑為比照文章建立惰性→eager 打分 + 一個 `excludeSpam` 等價之動態 feed 排除；惟須先評估動態之模型品質（共用短內容模型對動態之召回／誤殺未經本報告所載之 acceptance 驗證）。在偏差未驗證前，對動態貿然自動處置之風險，與 §5 文章模型之偏差教訓相同。

---

## 5. 模型更新：文章偵測模型之偏差與矯正

2026 年 6 月，文章偵測模型（granite-embedding-107m-multilingual，XLM-RoBERTa 序列分類器）經歷自奠基以來最重大之更新。

### 5.1 Conformal abstention serving（`spam-detection-serverless`）
- **commit `a4abd49` + PR #2（serverless，06-13）**：引入類別條件式 conformal 決策層。儲存每類校準分數陣列，推論時以二分搜尋求 p-value（O(log n)），保證 P(正當內容被封鎖 | 真 ham) ≤ ε，與類別先驗無關；輸出 **allow／block／review** 三態，灰區交付人工，每筆附處置理由（對應 DSA 第 17 條）。向後相容：仍回 `score`（matters-server 只讀此），新增 `decision`/`reason`/`p_spam`/`p_ham`。LOO：ε=0.02 → ham 誤封 1.8%、召回 93%。
- **PR #3（serverless，06-13）runbook**：staging 部署 runbook 與 shadow-eval；誠實註記「誤殺 ≤ ε」為期望／有限樣本性質，非硬保證。
- **PR #5（serverless，06-13）**：OIDC SAM 部署至**隔離** staging stack `article-spam-conformal-staging`，硬性拒絕生產 stack。

> **重要極限**：conformal 之 abstention 僅能挽救「分數落於灰區」之不確定案例；它**無法挽救「模型以高信度給出錯誤答案」**。下節之偏差，正是此極限之具體呈現——舊模型以 0.98–1.0 之高信度誤判華語內容，conformal 救不了。

### 5.2 偏差之診斷
於訓練截點後（`article.id > 1104414`）、模型未見之乾淨文章上驗收，舊模型以 **0.98–1.0 高信度**將華語政治評論（〈國安部"反躺平"〉、〈躺平〉、〈習近平首試"硬碰硬"〉）、時事分析、學術考據（〈散氏盤偽銘文〉）與個人創作（〈日記，超級生命密碼〉）判為 spam（→ 分數 ≥ 門檻 → 排除曝光）。量化（逐篇確認之真實標籤）：**真誤殺率 24%（@0.5）**。

**根因**：訓練標籤之垃圾正樣本很大一部分來自代理標籤「**作者被限制**（user_restriction）」——一位作者被限制，其名下**全部文章**即被標為 spam，包括其合法之政治評論、學術文與日記。模型遂習得危險捷徑：**「華語政治／異議書寫」之特徵 ≈ spam**。此為資料層之連坐，經模型固化、規模化、自動化。對抗審查平台，此為最不可接受之失敗模式——以反垃圾之名，行壓制異議之實，且自動而無人知曉。

### 5.3 矯正：修標籤，而非「重新平衡」
先前之「rebalance 重訓」（調整類別比例）將模型洗鈍（誤殺未降、召回反掉）。教訓：**偏差之根在標籤之錯誤，不在比例**。v1 直接矯正被連坐誤標之標籤（將合法內容翻回 ham 並加重學習）。

規模化標註不用外部 API/Bedrock，改以**平台同信任邊界內之 Claude subagent 平行標註**（`spam-detection-scaffold` PR #10 提供 LLM-judge 引擎與 136 筆 gold 驗證 gate；實際標註由 subagent 執行）：4 輪累計 **4,980 筆矯正標籤（2,649 hard-negative ham + 2,331 spam）**。重訓集 = 427k 基線套用矯正 + hard-negative ham 過採樣 ×5；SageMaker g4dn.xlarge，約 79 分鐘、約 1 美元。

**結果（625 篇 subagent 確認之乾淨 held-out）：**

| | 真誤殺（@0.5） | 垃圾召回 |
|---|---|---|
| 舊模型 | 24.0% | 97% |
| **v1（修標籤）** | **1.6%** | 92% |

矯正後 v1 對同批文章：政治評論 0.984→0.081、學術 0.996→0.067、日記 1.000→0.031（救回）；博弈 0.992→0.958、色情 1.000→0.999（仍攔）。**關鍵洞察：垃圾不由「主題」定義，而由「結構」定義**（關鍵詞堆砌、站外導流、為特定服務反覆置入、跨帳號機械重複）——v1 學會看結構。

### 5.4 部署
- v1 conformal calib（held-out v1 分數重建，LOO ε=0.02 ham 誤封 1.9%）→ staging 部署實機驗證（serverless PR #6，06-16）→ 經 `deploy-prod-conformal.yml`（confirm 防呆 + 失敗回滾）促進至生產 stack `article-spam-model-v20251229`。
- **生效範圍**：matters-server 文章路徑（`MATTERS_SPAM_DETECTION_API_URL`→v1）以 `spamScore ≥ 門檻` 排除文章；換 v1 後舊模型誤殺之政治／學術文 0.98→0.03–0.33 → **不再被埋沒（新內容即時生效）**；垃圾仍 ≥0.95 → 照樣排除。本期決策**不回溯重打分舊文章**。

---

## 6. 分數重新評估（score re-evaluation）：方法論與其陷阱

「分數重新評估」之嚴謹性，是本次矯正能成立之關鍵。其方法論本身值得記錄，因其每一步都曾被假數字誤導，又靠「先質疑數字」修正。

### 6.1 為何不能用訓練集 in-sample 評估
首次於 staging 以訓練集 ham 抽樣（in-sample，100+100）驗收，得 ham 被封鎖 ≈97%。此非真實誤殺——係訓練集 ham 標籤雜訊（大量純圖片／低文字文章 granite 給 ≈1.0）+ 504 冷啟動污染。結論：**正式驗收須用訓練截點後、逐篇確認之乾淨 held-out**（`scaffold/eval/staging_conformal_accept.py` 之 `--source replica`）。

### 6.2 量測管線之 bug
acceptance 一度顯示「100% block」之假象——根因為 payload 格式錯誤：以 JSON `{"text":...}` 送入文章端點，致其把 JSON 字串當文章評分→恆回 ≈1.0。改送 **raw body** 後方見真相（同文 RAW→0.337 allow vs JSON→1.0 block）。

### 6.3 代理污染：為何「裸誤殺 36%」是假象
以「老帳號、未受限」作為「乾淨合法內容」之代理計算，得誤殺 36%。然逐篇複核發現此代理約 **37% 其實是 spam**（老帳號亦接外送茶、做貸款 SEO；一個 escort 帳號已 818 天、883 篇）。以 5 個 subagent 用修正判準重判 500 篇 ham，挑出真 ham 313／真 spam 312，**去污染後真誤殺方為 1.6%**。

### 6.4 金融偽裝
數篇「防詐科普」「資產管理解析」一度被標為合法 ham；細察其作者為貸款／券商／討債帳號、結尾導流至自家產品——實為偽裝成知識之 SEO 垃圾。耐人尋味者：模型即便被錯誤地餵以這些當 ham，仍判其偏 spam——**模型比標註者更早嗅到了結構**。判準遂修正：貸款／券商／討債帳號發之「科普」+ 結尾導流 = 商業 SEO spam。

> **方法論教訓**：自動化內容治理最大之風險，不是模型不夠強，而是**評估時被自己的代理指標欺騙**。本次每一關鍵數字（97%／36%）皆為假象，唯有逐篇確認之乾淨樣本可信。分數重新評估之紀律——「先驗污染、再下結論」——與模型本身同等重要。

### 6.5 執行入口：VPC runner
正式 acceptance 需連 VPC 內之生產唯讀副本，GitHub runner 連不到，故建 CodeBuild-in-VPC（`scaffold` PR #7/#8/#9）。`heldout` job 撈訓練截點後乾淨 held-out 寫 S3，本地以 v1 與舊模型評分對比（PR #11 之 `--dump`）。

---

## 7. 主動攔截難偵測之重複樣態文章（軸一 D）

純內容打分對某類濫用無能為力：**「開新帳號→張貼重複文本」**。其定義訊號不是文章語意，而是**行為模式**——同一模板被大量 throwaway 新帳號重複張貼。本章記錄針對此類之主動攔截設計（`scaffold/docs/SPAM_ROADMAP.md` 軸一 D；POC `eval/ring_detect_poc.py`）。

### 7.1 動機：五群集之實證（2026-06-15）
對 5 個已知群集以公開搜尋（唯讀，不碰 DB）實測：

| 群集 | 篇/作者/模板族 | 最大 ring | 內容模型 |
|---|---|---|---|
| 老灯闲聊 | 30/30/3 | 1 模板跨 **18** 帳號 | 部分純圖看不到 |
| 披着律师外衣的搅局者 | 30/29/2 | 1 模板跨 **28** 帳號 | 詐騙長文可 block |
| 海外中国人权律师联盟 | 30/27/3 | 1 模板跨 **25** 帳號 | ✅ block 0.996 |
| 高雄翻译社 | 30/21/10 | 1 模板跨 **11** 帳號 | ❌ allow 0.091（漏） |
| live173影音live秀 | 30/28/24 | 1 模板跨 4 帳號 | ❌ allow 0.117（漏） |

內容模型漏掉高雄翻譯社（0.091）與 live173（0.117），但 ring 訊號抓得到——**證明行為訊號補得上內容打分之盲區**。

### 7.2 四個互補訊號（精確定義，`eval/ring_detect_poc.py`）
1. **`template_family` 精確模板指紋**：正規化（去 HTML、小寫、遮罩 url/@handle、數字→`#`）後取前 200 字之 md5 前 8 碼。與 `prepare_article_families.py` 同口徑。
2. **近似比對 ring（`neardup_groups`）**：char-4gram shingle 集合，Jaccard ≥ 0.5，union-find 連通。比精確模板多抓「模板有變化」之群——老灯 exact 3→近似 26 帳號、披着→29、海外→26、高雄→14。
3. **廣告實體 ring（`advertised_entities` / `entity_top_ring`）**：抽外部網域（`.com/.tv/.cc/.vip…`）+ 聯絡 id（line/telegram/wechat/微信…）。對 live173 此種「同服務、每篇文案都變」之廣告，**實體是不變量**——文字近似打不開時的解方（live173 近似 ring 4→實體 ring 10）。
4. **帳號名亂碼分數（`username_bot_score`，0–1）**：數字比例 >0.25（+0.4）、長子音串 5+（+0.3）、連續數字 3+（+0.2）、長度 ≥11 且 ≥2 數字（+0.1）。為 throwaway 新帳號之補充訊號（高雄翻譯社 100% 命中）。

> 三類訊號互補：文字型群集吃近似 ring，廣告型吃實體 ring，帳號名亂碼為共同補充。**任一訊號達門檻即列候選**，再走分級處置。

### 7.3 三層分級凍結設計
沿用 conformal「不確定就送人工」之精神，升至帳號層：
1. **行為偵測**：`template_family` group by、count distinct author、過濾新帳號／低 karma → 跨帳號重複 ring。正式版改吃 read-replica / L1 匯出之近期文章（SQL `sql/detect_spam_rings.sql`，DB 端粗篩、app 層近似精修，須 VPC 內執行）。
2. **證據合流**：ring 訊號 + 文章 conformal decision + 純圖旗標 三者合流（內容模型當輔助，不當唯一）。
3. **分級處置**：高信度（帳號新 **且** 跨帳號重複 ≥M **且**（內容 block 或純圖重複））→ 凍結；其餘 → 人工佇列。動作原語 = `user_restriction`/`archiveUser`。

### 7.4 安全護欄（凍結為高風險處置，硬性）
① **雙鑰**——≥2 獨立訊號才自動凍結，單訊號→人工；② **可逆 + 可申訴**（限制非刪除、通知本人，DSA 第 17 條一致）；③ **老帳號豁免**自動凍結（年齡/karma 過門檻）；④ **影子先行**——上線前「只記錄不動作」跑一週看誤傷再開真實處置。

### 7.5 閉環與弱點
- **閉環**：凍結即 `user_restriction`，而 L1 SQL 已把該訊號當正樣本 → 越凍越準。
- **弱點**：live173 文字變化太大（近似 ring 仍只 4）→ 需實體訊號補；繁簡字須正規化（「披著」繁體零結果、「披着」簡體 28 帳號）；`detect_spam_rings.sql` 須 VPC 內執行。

### 7.6 現狀
軸一 D 仍屬**候選／POC 階段**：偵測訊號（四訊號）已實證、SQL 與 VPC 執行入口已就緒（ring job 在 prod 全站跑通、候選寫 S3），但**分級凍結與影子先行尚未生產化**。依賴鏈：軸一 B 驗收（已過）→ C（佇列接帳號動作，目前 defer）→ D（ring 偵測 + 分級凍結）。

> 註：留言層另有獨立之 ring gate（海巡 bot `bot/ring.py`，已 live 並 Phase 2 自動打掃），與本文章層 ring 為不同層、互補——留言 ring 以守望相助移除史為證據基底，文章 ring 以跨帳號模板重複為訊號。

---

## 8. 訓練資料留存（文章／動態相關，軸二 L1/L2/L3）

為使處置決策之訓練訊號不因內容刪除而流失（`archiveUser` 只刪 draft/pending/error 文章；發佈文章與動態內容雖保留，但未來硬刪／GDPR 抹除有風險），建立三層留存。法務 2026-06-14 裁定保留期 1 年（S3 lifecycle 365 天）。

- **L1 被動增量抽取**：每日自唯讀副本抽——正樣本 = `user_restriction` 作者／`spamScore≥門檻`／守望相助 active 處置；hard-negative ham = 被推翻／還原之處置（誤殺）。SQL `extract_spam_training_incremental.sql`。
- **L2 主動快照**（#4846 + scaffold worker）：審查邊界 `enqueueSpamSample`→SQS（HMAC 去識別）→S3。AWS 基建已建並 smoke-test。
- **L3 標籤品質**：組裝器去重、ham 覆蓋 spam、`label_weight`（人工 2.0／受限-管理 1.0／僅模型 0.5）+ 規模化標註引擎（PR #10）。
- **閉環**：軸一 D 之凍結 → `user_restriction` → L1 正樣本；conformal review 之人工裁決 → L2 高 weight 標籤。

---

## 9. 群眾參與與人工訊號之回流

雖然文章治理以機器為主，人工與群眾訊號仍以兩種方式回流模型：
1. **管理員 `setSpamStatus`**（#4122/#4703）：逐篇人工判定寫入 `is_spam`，**永遠凌駕模型分數**（§3.2），且為 L1/L3 之最高權重正樣本。
2. **守望相助之推翻（reversed/restored）**：被人工推翻之處置 = 誤殺證據 → L1/L2 之 hard-negative ham，專治「受保護內容稀少」之問題。此即留言層之群眾協作審查，如何反哺文章／全平台模型之標籤品質。

此外，#4856 之 OSS `mostSpam` 分流，使管理員得以高效審視高分文章／動態／留言並施以人工判定——人工判定再經上述路徑回流，形成「機器初篩 → 人工確認 → 標籤回流 → 模型精進」之閉環。

---

## 10. 現狀、缺口與待辦

**已上線（文章層）**：文章 v1 模型 + conformal 已 live on prod，偏差修復對新內容即時生效；自動發現面排除成熟運作；訓練留存 L1–L3 程式 + L2 基建就緒；#4856 後台 spam 分流上線。

**缺口與待辦**：
- ⚠️ **動態（moment）無自動處置**（§4.2）——僅打分 + 後台分流。若要處置須先驗證共用短內容模型對動態之品質，再比照文章建排除（避免重蹈 §5 偏差覆轍）。
- ⏸️ **軸一 C**（matters-server 讀文章 conformal `decision` → 人工 review 佇列）：目前 **defer 進 backlog**——v1 分數雙峰分離、0.94 附近 review 灰帶幾乎無料，ROI 低；且現行程式僅讀 `score` 走硬門檻、未讀 `decision`。
- ⏸️ **軸一 D 生產化**：四訊號偵測與 VPC 執行已就緒，但分級凍結 + 影子先行未上線。
- ⏸️ 動態專用模型端點未拆分（仍用共用短內容模型）。
- 本期決策：不回溯重打分舊文章。

---

## 附錄：完整變動對照

### A. `matters-server`——文章／動態 spam（依紀元）
**2024 奠基**：#4117（spam_score/is_spam 欄位 + SpamDetector + feed 排除 + 「不刪除只是不再被看見」）、#4122（setSpamStatus + OSS）、#4129（excludeSpam 修正）、#4210/#4216/#4221（campaign/search 排除之反覆）。
**2025**：#4264（白名單 bypassSpamDetection）、#4266（預設啟用）、#4372（重試）、#4379（IPFS gating）、#4455（spam 略過語言偵測）、commit `aed2836e1`（拆 publicationService）、#4578（頻道修正）、commit `463c58fbe`/`5d3217b50`/`9d7c79873`（isSpam/分數邏輯）、commit `3a7cfda47`（moment detectSpam）、#4703（setSpamStatus 擴及留言/動態）、#4702（Comment/Moment spamStatus 欄位 + 短內容端點）、#4706（OSS moments + 惰性觸發）、#4707/#4708（readSpamStatus 旗標）、#4711（v5.20.0 發布）。
**2026**：#4838（留言專用端點，動態未拆）、#4843（留言自動折疊，共用門檻/白名單）、#4846（L2 擷取）、#4856（OSS mostSpam 分流——動態分數唯一消費者）。

### B. `spam-detection-serverless`——文章模型 serving 與部署
commit `a4abd49` + PR #2（conformal abstention serving）、PR #3（staging runbook/shadow-eval）、PR #5（OIDC staging 部署）、commit `3117756`/`bb18dc7` + PR #6（v1 label-fix calib + 生產促進工作流）。

### C. `spam-detection-scaffold`——驗收、主動攔截、標註、留存、基建
PR #5/#6（軸一 D ring POC/v2：template_family/近似/實體/亂碼訊號 + detect_spam_rings.sql）、PR #7/#8/#9（VPC CodeBuild runner，解鎖 ring SQL/acceptance/L1）、PR #10（規模化 LLM-judge 標註引擎）、PR #11（held-out dump，文章 acceptance）、commit `3ecccf3`/`b09a8b8`/`4603589`/`460e3db`（L1/L2/L3）、`SPAM_ROADMAP.md`（軸一 A/B/C/D + 軸二 L1–L3 總圖）。
