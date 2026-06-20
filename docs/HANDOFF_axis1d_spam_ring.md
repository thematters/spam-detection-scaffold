# Handoff — 軸一 D 正式版：Spam Ring 偵測 → OSS 控制台 → 一鍵凍結

> 狀態：2026-06-20。Phase 0/1/2 程式碼完成、本機驗證、已 commit（未 push）。
> 剩 **Phase 3（OSS 控制台 UI）**、Phase 1 的 **CI jest 驗證**、Phase 2 的 **部署/排程**。
> 三個 repo 都在 `~/Developer/repos/`。完整設計另見 `~/.claude/plans/stateless-churning-elephant.md`。

## 這是什麼

把「spam ring（開新帳號→貼重複模板的垃圾集團）」做成可治理的閉環，三件事：
1. **偵測**抓出既有內容模型漏掉的類別（crypto 邀請碼、博弈品牌詞）。
2. 把群集資料**整合進 OSS 控制台**。
3. 管理者**一鍵「凍結」整群**（取代過去手動一個個刪帳號）。

凍結＝對群內每個帳號**永久但可逆**封禁。

## 進度與分支

| Phase | 內容 | 狀態 | repo / 分支 / commit |
|---|---|---|---|
| 0 | 偵測訊號擴充（邀請碼/品牌/白名單） | ✅ 本機驗證 | spam-detection-scaffold `feat/ring-signals-codes-brands` `76acb54` |
| 1 | matters-server 後端（3 表＋4 mutation＋OSS 查詢＋service＋測試） | ✅ tsc/lint/14 測試綠 | matters-server `feat/spam-ring-detection` `f6e11c40d`,`b0a8cf08d` |
| 2 | 偵測 job：粗篩→精修→寫回 server | ✅ 純函式測試綠 | spam-detection-scaffold `feat/ring-signals-codes-brands` `b689a1f` |
| 3 | **matters-oss 控制台 UI** | ⬜ 未開始 | matters-oss（建議分支 `feat/spam-ring-console`，base develop） |

⚠️ 所有 commit **尚未 push、未開 PR**。matters-server 測試需 Postgres → 只能 CI 驗。

---

## Phase 3（待做）— matters-oss 控制台 UI

**目標 app＝舊 matters-oss**（CRA + antd v3 + Apollo 2）。使用者知情後仍選它求最快止血
（新 app `matters-oss-next` 僅 M0 鷹架，本頁未來需重做；後端 GraphQL 前端無關可重用）。

照既有模式新增一頁，**列 ring 候選 + 一鍵凍結/解凍/標記誤判**：

- 頁面 `src/pages/SpamRingList/index.tsx` + `src/hocs/withSpamRingList.tsx`
  （照 `src/hocs/withUserList.tsx` / `pages/ArticleList` 的 graphql HOC + antd Table 模式）。
- gql：`src/gql/queries/spamRingList.gql`（查 `oss.spamRings`）；
  mutations `freezeSpamRing.gql` / `unfreezeSpamRing.gql` / `dismissSpamRing.gql`。
- 表格欄位：fingerprint + memberSample、訊號（signals.nearDupRingSize / entityRingSize /
  botUsernameRatio / topEntity / sampleCodes / sampleBrands）、nAuthors / nArticles、
  newAccountRatio、status、detectedAt、score；支援 status 過濾、score/detectedAt/nAuthors 排序。
- 列動作「**凍結整群**」→ 確認 modal（顯示將封成員數、**老帳號將被略過的預覽**、可逆＋會發通知說明）
  → 呼叫 `freezeSpamRing`，回傳 `{frozen, skipped}` 後 toast 顯示封了幾個、略過幾個。
  另加「解除凍結」「標記誤判」。元件照 `src/components/User/SetState/`、`User/ToggleBypassSpam/`
  的 mutation-component 模式。
- 路由：`src/constants/route.ts` 加 `SPAM_RING_LIST`；`src/routes/` 註冊；
  `src/components/Layout/Sider/index.tsx` 加左選單。
- stack 一致：CRA + antd v3 + Apollo 2（legacy）；build 需 `NODE_OPTIONS=--openssl-legacy-provider`。

### Phase 3 要用的 GraphQL 合約（已在 matters-server 實作）

```graphql
# 查詢（oss 已 @auth(admin)，子欄位免再 gate）
oss {
  spamRings(input: { first: 50, sort: score, filter: { status: pending } }) {
    totalCount
    edges { node {
      id fingerprint status score severity nArticles nAuthors newAccountRatio detectedAt
      signals { nearDupRingSize entityRingSize botUsernameRatio topEntity sampleCodes sampleBrands }
      memberSample(limit: 5) { id userName state }
      members(input: { first: 20 }) { totalCount edges { node {
        id status bannedByThisRing skipReason user { id userName state }
      } } }
    } }
  }
}

mutation { freezeSpamRing(input: { id: $ringGlobalId, remark: "spam ring" }) {
  ring { id status frozenAt }
  frozen { id userName }
  skipped { user { id userName } reason }   # 老帳號/高karma/已封/已archive → 人工複查
} }

mutation { unfreezeSpamRing(input: { id: $ringGlobalId }) {
  ring { id status } unbanned { id userName } skipped { user { id } reason }
} }

mutation { dismissSpamRing(input: { id: $ringGlobalId, note: "false positive" }) { id status } }
```

`SpamRingStatus = pending | frozen | dismissed | restored`；`SpamRingsSort = score | detectedAt | nAuthors`。
排序預設 score desc。

---

## Phase 1（已做）— matters-server 後端細節

分支 `feat/spam-ring-detection`（off develop）。關鍵檔：

- **Migrations**（`db/migrations/`）：`20260620000000_create_spam_ring_table.js`、
  `..._member_table.js`（含 `banned_by_this_ring` 旗標）、`..._event_table.js`（稽核）。
- **Service** `src/connectors/spamRingService.ts`：`findRings/findMembersAndCount/findEvents/
  upsertCandidates/freezeRing/unfreezeRing/dismissRing`。重用 `userService.banUser/unbanUser/findScore`
  （**userService 由 resolver 注入**，非內部 new，為了可測）。
- **Mutations** `src/mutations/user/{freeze,unfreeze,dismiss}SpamRing.ts`、`upsertSpamRingCandidates.ts`。
- **Query** `src/queries/system/oss/spamRings.ts`；型別 resolver `src/queries/system/spamRing/index.ts`。
- **SDL**：`src/types/system.ts`（SpamRing 型別群＋OSS.spamRings）、`src/types/user.ts`（4 mutation）。
- **註冊**：`connectors/index.ts`、`routes/graphql.ts`（dataSources）、`definitions/index.d.ts`
  （DataSources 型別 + TableTypeMap + export）、`codegen.json`（**mappers 加 SpamRing 系列**）、
  `mutations/user/index.ts`、`queries/system/oss/index.ts`、`queries/system/index.ts`。
- **enums**：`common/enums/index.ts`（NODE_TYPES.SpamRing/Member/Event）、`common/enums/user.ts`
  （USER_BAN_REMARK.spamRing = 'spam ring'）。

### 凍結語意（核心，已驗證）
`userService.banUser(id, { remark })` **省略 banDays** → 不寫 punish_record、無到期、`state='banned'`、
仍發 `user_banned` 申訴通知 → 永久但可逆；`unbanUser(id, 'active')` 還原。
`spam_ring_member.banned_by_this_ring` 確保解凍**只**還原本 ring 造成的封禁，不動他因已封的帳號。

### 護欄（roadmap 軸一 D，硬性）
freezeRing 逐成員：帳齡 `>30d` 或 `authorScore >5` → **跳過不自動封**、列 `skipped` 人工複查
（常數 `SPAM_RING_GUARDRAIL_MAX_AGE_DAYS=30` / `_MAX_SCORE=5` 在 spamRingService.ts 模組頂）。
已 archived/已 banned → 跳過。被封帳號 `state=banned` 已被 L1 SQL 自動收為正樣本 → 訓練閉環。

### 測試（已寫，本機 14/14；CI 需 PG 跑全套 + codecov）
`src/connectors/__test__/spamRingService.test.ts`（mock-knex：freeze 護欄/ban/事件、unfreeze 可逆、
dismiss）、`src/common/utils/__test__/spamRingMigration.test.ts`、`...spamRingResolvers.test.ts`。
全 mock，不需 PG。

---

## Phase 2（已做）— 偵測 job

分支同 Phase 0（`feat/ring-signals-codes-brands`）。
- `eval/ring_signals.py`：共用訊號模組（POC 與 job 同口徑）。`advertised_entities` 已加
  邀請碼(`invite:`)、品牌詞(`brand:`)、主流網域白名單。
- `sql/detect_spam_rings.sql`：DB 端粗篩，輸出加 `author_ids` / `article_ids`。
- `scripts/ring_detect_job.py`：跑粗篩 → 抓候選 ring 內容 → ring_signals 精修 → 組 candidate →
  呼叫 `upsertSpamRingCandidates`。**影子先行**：未設 endpoint/token 只印不寫；支援 `DRY_RUN`。
- `scripts/test_ring_detect_job.py`：純函式測試（本機 4/4）。
- `infra/vpc-runner/buildspec.yml` 的 `JOB=ring` 已接上。

### Phase 2 部署待做（需 infra/AWS，本機無法驗）
1. 設 `MATTERS_OSS_GQL_ENDPOINT`（matters-server graphql）+ `MATTERS_OSS_ADMIN_TOKEN`
   （admin service principal token）給 CodeBuild env 或 SSM。**admin token 取得方式待定**
   （matters-server admin 認證 header；`ring_detect_job._post_upsert` 目前同送 Authorization Bearer
   與 x-access-token，依實際機制收斂）。
2. EventBridge rule 或 GH Action cron 觸發
   `aws codebuild start-build --project spam-vpc-runner -e name=JOB,value=ring`（每日/每週）。
3. 端到端驗證：先 DRY_RUN（看候選品質），再開寫回，觀察 OSS 控制台一週候選，最後才由人手動凍結。

---

## 關鍵決策（不要回頭推翻）

- 凍結 = 永久可逆 banUser（非 archiveUsers——archive 不可逆、要密碼、上限 50，違反可逆護欄）。
- 控制台蓋舊 matters-oss（知情選擇求最快上線）。
- 候選由 scaffold VPC job 寫回 server（server 為唯一真相源；OSS 只跟 server 講話）。
- 本版只做**手動**一鍵凍結（admin click＝人工關卡＝de facto shadow-first）；自動凍結＋雙鑰留後續。
- 暫不做純圖證件文（破口③，需標題/感知雜湊，另案）。
- upsert 對 fingerprint idempotent、**不覆寫**已 frozen/dismissed 的決策。

## Gotchas

- **codegen 雞生蛋**：新增 GQL 型別+resolver 時，`gen:schema` 的 `tsc && exportSchema` 會因 resolver
  參考尚未生成的型別而 tsc 非 0 → `&&` 斷鏈。解法：手動 `npx tsc -p .`（noEmitOnError 為 false，仍 emit）
  `;` `node build/common/utils/exportSchema.js` 重生 schema.graphql → `npm run gen:types` 生 schema.d.ts
  → 再 `npx tsc -p .` 驗證。`schema.graphql` 與 `src/definitions/schema.d.ts` 是 committed 生成檔，要一起提交。
- **本機無 PG**：matters-server 全套 jest 與 codecov 靠 CI；本機只能跑 mock 測試（見上）。
- **pre-commit hook 會污染全 repo**（prettier/codegen 版本漂移）：matters-server/oss 一律
  `git commit --no-verify`、只 stage 自己的檔。
- **zsh 不對未加引號變數斷詞**：批次傳檔給 eslint 用 `${=FILES}`。
- matters-oss build 需 `NODE_OPTIONS=--openssl-legacy-provider`。

## 驗證指令（重跑）

```bash
# Phase 0/2 偵測（scaffold，venv 在 /tmp/ringpoc-venv）
cd ~/Developer/repos/spam-detection-scaffold
/tmp/ringpoc-venv/bin/python eval/test_ring_signals.py            # 11/11
/tmp/ringpoc-venv/bin/python scripts/test_ring_detect_job.py     # 4/4
/tmp/ringpoc-venv/bin/python eval/ring_detect_poc.py "币安邀请码" "28BET"   # 線上回歸（需網路）

# Phase 1 後端（matters-server）
cd ~/Developer/repos/matters-server
npx tsc -p .                                                       # 0 errors
MATTERS_ENV=test node --experimental-vm-modules --no-experimental-fetch node_modules/.bin/jest \
  build/connectors/__test__/spamRingService.test.js \
  build/common/utils/__test__/spamRingMigration.test.js \
  build/common/utils/__test__/spamRingResolvers.test.js --coverage=false   # 先 tsc 再跑；14/14
```

## 給 Codex 的下一步建議

1. **Phase 3**：在 matters-oss 開 `feat/spam-ring-console`，照上面合約與既有模式做 SpamRingList 頁
   + 凍結/解凍/標記誤判。用 staging matters-server 實機點測（瀏覽器截圖工具不穩，以 staging 為準）。
2. 開 PR（**server 先上**，因前端依賴新 schema；matters-server develop→部署後 oss 才指過去）。
3. 協助 Phase 2 部署（admin token 機制、EventBridge 排程）。
4. matters-server PR 在 CI 跑全套 jest + codecov，補不足的 patch 覆蓋。
