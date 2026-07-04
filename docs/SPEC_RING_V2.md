# SPEC: Spam Ring 治理 v2 — 單帳號重複規則、自動凍結、Telegram 日報、發現面觀察期

| Field | Value |
| --- | --- |
| Feature / change name | Spam Ring v2（F1 門檻/單帳號、F2 Telegram 日報、F3 純圖成員過濾＋自動凍結、新帳號發現面觀察期） |
| Owner (human, approves the SPEC) | mashbean |
| Drafted by | agent（Claude，依 2026-07-04 需求訪談） |
| Risk tier | **Full**（moderation/spam 風險面） |
| Date | 2026-07-04 |
| Status | Approved（2026-07-04 使用者於訪談中逐項拍板，見 §12） |

## 1. Problem & User

Matters 站上免洗帳號持續灌垃圾文。既有軸一 D ring 偵測（影子週已驗證：雙鑰＋老帳號豁免
整週 0 老帳號誤入 FREEZE）目前為「每日偵測 → 控制台 pending → 人工一鍵凍結」。痛點：

1. **單帳號洗文抓不到**：偵測只看跨帳號模板重複，一個帳號自貼 N 篇重複文不成 ring。
2. **人工延遲**：管理員要自己記得去控制台看；沒有主動提醒。
3. **純圖假 ring 誤傷**：SQL 粗篩把「分享圖片無內文」的動態湊進 ring，`ring_detect_job.py`
   只在「整個 ring 最常見指紋＝空」時跳過整個 ring，**沒有成員級過濾**——混合 ring 裡的
   純圖老帳號被一併掛進成員名單，是真人被誤列的主因，也擋住了自動化。
4. **凍結速度**：FREEZE 級 ring 明明面貌乾淨（影子週實證），還是要等人工，spammer
   補帳號的窗口以天計。
5. **誘因未被攻擊**：凍結是事後追擊；免洗帳號發文的曝光/SEO 價值仍在。

## 2. Goal & Non-Goals

- **In scope:**
  - **F1a** 單帳號重複規則：同帳號同模板 ≥3 篇 → 列為候選（送控制台，**永不自動凍結**）。
  - **F1b** FREEZE 鑰1 門檻：跨帳號數 5 → **3**（佐證鑰、老帳號豁免不變）。
  - **F2** 每日固定時間 Telegram 日報：pending ring 數、FREEZE/REVIEW 分佈、top rings、
    控制台連結；走 matters-server 既有 Telegram 通知環境（`MATTERS_TELEGRAM_BOT_TOKEN`
    / `MATTERS_TELEGRAM_ALERT_CHAT_ID`），比照 `dailySummaryEmail` handler 部署模式。
  - **F3a** 成員級純圖過濾：組 candidate 時剔除「該 ring 內貼文全為正規化後空內容」的成員；
    過濾後跨帳號數低於門檻的 ring 丟棄；`new_account_ratio`／訊號以**過濾後成員**重算。
  - **F3b** 自動凍結 FREEZE 級：job 端對「過濾後仍雙鑰成立、非老帳號豁免」的 ring 呼叫
    `freezeSpamRing`（server 端本就逐成員略過老帳號/高 karma，為第二道網）。
    以 `AUTO_FREEZE` env 閘門，預設 off（Dark）。
  - **觀察期**：新帳號（帳齡 < N 天，N 可調，預設 3）文章不進發現面
    （hottest/newest/頻道/標籤），直接連結、個人頁、追蹤流不受影響。
    feature flag `discovery_probation` 預設 **off**（Dark 進 develop）。
  - server 端小幅擴充：`UpsertSpamRingCandidatesResult` 增回 `rings { id fingerprint status }`
    （additive，供 job 端拿 global id 呼叫 freezeSpamRing）。
- **Out of scope (explicitly not doing):**
  - 快車道（發佈當下指紋比對已確認 ring → 秒級凍結）——下一輪，見 §11。
  - 實體黑名單（brands/codes 萃取）——下一輪。
  - 前端 noindex／meta robots（matters-web）——觀察期 flag 開啟前補。
  - 註冊端任何防機器人措施（明確不做：保護匿名吹哨者/記者）。
  - 單帳號重複候選的自動處置（只進控制台＋日報）。

## 3. Success Criteria

| # | Acceptance condition | How it's verified |
| --- | --- | --- |
| 1 | 純圖無內文動態不再把帳號帶進 ring 成員（混合 ring 中該類成員被剔除） | scaffold 單元測試＋DRY_RUN 對 replica 實跑抽查 |
| 2 | 單帳號同模板 ≥3 篇成為控制台候選（nAuthors=1，不自動凍結） | 單元測試＋DRY_RUN 實跑 |
| 3 | AUTO_FREEZE=1 時，雙鑰（跨 ≥3 帳號＋新帳號比 ≥0.8 或亂碼比 ≥0.5）且非老帳號豁免的 ring 被自動凍結；老帳號豁免 ring 一律 pending | 單元測試（決策函式）＋影子欄位 `autoFreezeEligible` 比對 |
| 4 | 日報每日固定時間送達 Telegram 管理群，含 pending 統計與控制台連結 | staging 手動 invoke handler → 群組收到訊息 |
| 5 | flag off 時觀察期零行為變化；flag on 時新帳號文章不在 hottest/newest/頻道/標籤 feed，直接連結仍可讀 | server 單元測試（flag on/off 兩態）＋staging 驗收 |
| 6 | 自動凍結不碰帳齡 >60 天或高 karma 帳號（server 端 skip 生效） | freezeSpamRing 既有測試＋自動凍結後 skipped 清單稽核 |

## 4. Repos & Service Placement

| Work area | Repo | Core / Non-core | Reason |
| --- | --- | --- | --- |
| 偵測/過濾/自動凍結 job | spam-detection-scaffold | Non-core | 既有軸一 D job 所在 |
| upsert 回傳 rings、Telegram 日報 handler | matters-server | Core | GraphQL schema／既有 Telegram env 與 handler 部署模式 |
| 觀察期（發現面排除） | matters-server | Core | 公開 feed query 所在（比照 #4891 掛點） |
| 排程 | EventBridge Scheduler | Infra | ring job 每日 → 每 4 小時；日報每日一次 |

## 5. Data & Schema

- 資料表：無新表。`feature_flag` 新增一列 `discovery_probation`（migration，預設 off）。
- GraphQL：`UpsertSpamRingCandidatesResult.rings: [SpamRing!]!`（additive）。
- SQL（scaffold）：`detect_spam_rings[_moment].sql` 粗篩條件加
  `OR (n_authors = 1 AND n_posts >= :single_author_min_posts)`；content 查詢帶
  `u.created_at`（成員過濾後重算 new_account_ratio 用）。
- Migrations/backfill：無資料回填。既有控制台 pending 中的純圖假 ring 由管理員以
  「標記誤判」清除（日報上線後自然浮現）。

## 6. Permissions

| Actor | May do | Must NOT do | Enforced by |
| --- | --- | --- | --- |
| VPC runner（admin token） | upsertSpamRingCandidates、freezeSpamRing | 碰帳齡>60d/高 karma 帳號（server 逐成員 skip） | `@auth(mode: admin)`＋server 端 skip 邏輯 |
| 日報 handler（Lambda） | 讀 spam_ring 表、發 Telegram | 任何寫入 | 唯讀查詢；無 mutation |
| 一般使用者 | 不受影響 | — | 觀察期僅影響發現面排序來源，無新 API |

## 7. Risk Class

| Risk surface | Touched? | Boundary |
| --- | --- | --- |
| Moderation / spam / blocklist | **Yes** | 自動凍結門檻邏輯（job）＋freezeSpamRing（既有，server 端護欄不動） |
| Account state / permissions | **Yes** | 僅透過既有 freezeSpamRing（frozen 可逆、有申訴通知）；無新 state |
| Email / notifications | Yes（輕） | 新增 Telegram 日報（唯讀彙總，不含 PII，只有帳號名/計數） |
| Payments / Federation / Auth / Upload / Routing | No | — |

→ **Full tier**：security review 是上 production 前的硬性關卡（見 §10 rollout）。

## 8. Feature-Flag Plan (Dark-launch)

| Field | Value |
| --- | --- |
| State while in progress | Dark |
| Flag name | `discovery_probation`（FEATURE_NAME 新增）；scaffold 側 `AUTO_FREEZE` env（預設未設＝off） |
| Default state | `off` |
| Back-end guard | 發現面 query（hottest/newest/tag/channel）套 modifier 前檢查 `isFeatureEnabled` |
| Front-end gating | 無前端變更（noindex 為後續 web 工作） |
| Launch trigger | 觀察期：產品端確認 N 值後 `setFeature` on。AUTO_FREEZE：security review 過＋F3a 過濾上線後首週人工抽查 FREEZE 決策無誤傷，由 owner 在 runner env 開啟 |
| Kill-switch | `setFeature` → off；runner env 移除 `AUTO_FREEZE`；凍結全部可逆（unfreezeSpamRing） |

## 9. Design Surface

無新 UI（控制台沿用既有 rings 頁；Telegram 為文字訊息）。

## 10. Rollout / 驗收順序

1. scaffold PR（F1/F3a/F3b code，AUTO_FREEZE 預設 off）merge → 下次排程生效（行為不變，多了單帳號候選與成員過濾）。
2. server PR（upsert rings 回傳＋日報 handler）merge → develop → staging 驗收 → promote。
3. 日報 EventBridge 排程建立（每日 09:00 台北）。
4. 觀察期 PR（Dark）merge → staging flag on 驗收 → 產品拍板 N → prod flag on。
5. **security audit（matters-agent-sop skills/security-audit）跑過、無 CRITICAL/HIGH** → 開 AUTO_FREEZE。
6. AUTO_FREEZE 開啟首週：日報加註自動凍結清單，人工抽查；異常即關 env。

## 11. Future work（本輪明確不做，下一輪候選）

- 快車道：發佈時 normalized_fingerprint 比對「人工已確認凍結」ring → 秒級凍新成員。
- 實體黑名單：從已確認 ring 的 sampleBrands/sampleCodes 萃取，命中即進 REVIEW。
- matters-web：觀察期帳號文章 noindex；「已凍結用戶」既有 UI 已齊。
- 單帳號重複候選的分級自動處置（累積數據後再議）。

## 12. 決策紀錄（2026-07-04 訪談）

| 問題 | 拍板 |
| --- | --- |
| F1「三項重複內容」語意 | **兩者都要**：單帳號 ≥3 重複文成候選＋FREEZE 門檻 5→3 |
| F3 自動化程度 | **直接開自動凍結 FREEZE 級**（修完純圖過濾後；仍以 AUTO_FREEZE env 閘門與 security review 為前置） |
| F2 Telegram 管道 | 走 matters-server 既有 Telegram 通知設定（reportTelegramAlert 同一組 env） |
| 觀察期 | **一起做、直接實作（暗啟動）**，feature flag 預設 off |
