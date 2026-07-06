# VPC 內執行入口（CodeBuild-in-VPC）

GitHub-hosted runner 與本機都連不到 prod read-replica（`matters-prod-replica-analysis` 在 VPC 內、DB SG 只放行 VPC 內來源）。這是三個工項的共同瓶頸：

- **軸一 D**：`sql/detect_spam_rings.sql`（文章層 ring 偵測，讀近期文章）
- **軸一 B**：`eval/staging_conformal_accept.py`（conformal 定論驗收，抽乾淨 held-out ham）
- **軸二 L1**：`scripts/export_training_samples.py`（被動增量抽取訓練樣本）

本目錄用一個**掛進 prod VPC 的 CodeBuild 專案**當共用執行入口，一次解鎖三者。

## 為什麼 CodeBuild（而非 Lambda / 常駐 runner）

| 方案 | 取捨 |
| --- | --- |
| **CodeBuild-in-VPC（採用）** | 臨時（用完即銷、零常駐成本）、可跑任意腳本、最長 8h、掛 VPC、讀 SSM、寫 S3、手動/排程/GH 觸發 |
| VPC Lambda | 15 min 上限，驗收評分上千篇會超時；打包依賴麻煩 |
| 常駐 EC2/ECS runner | 要顧維運與安全更新；有常駐成本 |
| 自建 GH VPC runner | 維運負擔最大 |

## 一次性建立（AWS admin 執行）

```bash
bash infra/vpc-runner/setup.sh
```

建立：
- IAM 角色 `spam-vpc-runner`（CodeBuild assume），最小權限（`codebuild-role-policy.json`）：
  讀**唯一**一個 SSM 參數（replica DSN）、KMS decrypt（限 ssm）、寫 `matters-spam-training-samples`、
  VPC ENI 權限、CloudWatch logs。
- runner 專屬 SG（只出不進）。
- CodeBuild 專案 `spam-vpc-runner`，掛 VPC `vpc-02362a4fe3806ffac` 的三個私有子網。

### ⚠️ 唯一的 prod 網路變更
`setup.sh` 會對 replica 的 DB SG `sg-0aff7c791291d103d` 加一條 inbound：允許 runner SG 連 5432。
這是讓 CodeBuild 連得到 replica 的必要條件。**可逆**——還原：

```bash
aws ec2 revoke-security-group-ingress --region ap-southeast-1 \
  --group-id sg-0aff7c791291d103d --protocol tcp --port 5432 --source-group <runner-sg-id>
```

若執行者無權改 DB SG，setup.sh 會略過並提示，請 ops 補這條規則。

## 觸發

```bash
# 軸一 D：文章層 ring 偵測（候選寫 S3，不做任何處置）
aws codebuild start-build --region ap-southeast-1 --project-name spam-vpc-runner \
  --environment-variables-override name=JOB,value=ring

# 軸一 D：watchlist read-only sanity check（不寫候選、不凍結）
aws codebuild start-build --region ap-southeast-1 --project-name spam-vpc-runner \
  --environment-variables-override name=JOB,value=ring-watchlist

# 軸一 B：conformal 定論驗收
aws codebuild start-build --region ap-southeast-1 --project-name spam-vpc-runner \
  --environment-variables-override name=JOB,value=acceptance

# 軸二 L1：訓練樣本匯出
aws codebuild start-build --region ap-southeast-1 --project-name spam-vpc-runner \
  --environment-variables-override name=JOB,value=l1
```

排程：之後可加 EventBridge 規則定時 `start-build`（L1 每日、ring 每日/每週）。

## 安全邊界
- DSN 走 SSM SecureString，CodeBuild 注入環境變數，**不落地、不印出、不進 git**。
- 角色只讀**那一個** SSM 參數、只寫訓練桶；無其他 prod 權限。
- runner SG 只出不進；對 DB SG 的 inbound 是唯一 prod 網路變更，且可逆。
- ring 工作**只找候選、不做處置**；處置分級（凍結 vs 人工）遵守 `SPAM_ROADMAP` 軸一 D 安全護欄。
- private subnet 需有 NAT egress 才能 pip install + 呼叫 staging endpoint；連 replica 走 VPC 內網（不經 NAT）。

## 後續小補（不影響本入口建立）
- `ring` 路徑目前只跑 DB 端 SQL 把候選寫 S3（核心解鎖）。app 層近似精修沿用
  `eval/ring_detect_poc.py` 的 neardup/username 邏輯，可後續加一支讀 CSV 的 `ring_refine.py`。
- `acceptance` / `l1` 的 buildspec 用了 `--dsn-env PG_DSN` 介面；對應腳本
  （`staging_conformal_accept.py`、`export_training_samples.py`）目前是公開搜尋/本機版，
  需小幅補上「從環境變數讀 DSN 連 replica」的入口（schema 已在 `sql/` 對齊）。
