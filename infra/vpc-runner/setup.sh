#!/usr/bin/env bash
# 一次性建立「VPC 內執行入口」= 掛進 prod VPC 的 CodeBuild 專案。
# 解鎖所有「需要連 read-replica」的批次工作（GitHub-hosted runner 連不到 replica）：
#   - 軸一 D：sql/detect_spam_rings.sql（文章層 ring 偵測）
#   - 軸一 B：eval/staging_conformal_accept.py（conformal 定論驗收，乾淨 held-out ham）
#   - 軸二 L1：scripts/export_training_samples.py（被動增量抽取）
#
# 為什麼 CodeBuild：臨時（用完即銷、無常駐成本）、可跑任意腳本、最長 8h（Lambda 只有 15min）、
# 可掛 VPC、讀 SSM DSN、寫 S3、可手動/排程/GH Actions 觸發。
#
# 用 AWS admin 憑證執行：bash infra/vpc-runner/setup.sh
# 冪等：重跑安全（已存在則更新）。
set -euo pipefail

ACCOUNT_ID=903380195283
REGION=ap-southeast-1
HERE="$(cd "$(dirname "$0")" && pwd)"

PROJECT=spam-vpc-runner
ROLE_NAME=spam-vpc-runner
VPC_ID=vpc-02362a4fe3806ffac
SUBNETS="subnet-0b011dd1ca64fa0a1,subnet-08074bc162cd5a4a3,subnet-0415147ddf68a48f2"
DB_SG=sg-0aff7c791291d103d         # replica 的 DB security group（5432）
SOURCE_REPO="https://github.com/thematters/spam-detection-scaffold.git"

# 1) CodeBuild 執行角色（被 codebuild.amazonaws.com assume）
TRUST='{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"codebuild.amazonaws.com"},"Action":"sts:AssumeRole"}]}'
if aws iam get-role --role-name "$ROLE_NAME" >/dev/null 2>&1; then
  echo "Role $ROLE_NAME exists; updating trust ..."
  aws iam update-assume-role-policy --role-name "$ROLE_NAME" --policy-document "$TRUST"
else
  echo "Creating role $ROLE_NAME ..."
  aws iam create-role --role-name "$ROLE_NAME" --assume-role-policy-document "$TRUST" \
    --description "VPC CodeBuild runner for replica-bound spam jobs"
fi
aws iam put-role-policy --role-name "$ROLE_NAME" --policy-name spam-vpc-runner \
  --policy-document "file://${HERE}/codebuild-role-policy.json"
ROLE_ARN="$(aws iam get-role --role-name "$ROLE_NAME" --query Role.Arn --output text)"

# 2) 給 runner 專屬 security group（egress only；inbound 不開）
if ! SG_ID=$(aws ec2 describe-security-groups --region "$REGION" \
      --filters "Name=group-name,Values=${PROJECT}-sg" "Name=vpc-id,Values=${VPC_ID}" \
      --query 'SecurityGroups[0].GroupId' --output text 2>/dev/null) || [ "$SG_ID" = "None" ]; then
  echo "Creating SG ${PROJECT}-sg ..."
  SG_ID=$(aws ec2 create-security-group --region "$REGION" --group-name "${PROJECT}-sg" \
    --description "spam-vpc-runner egress" --vpc-id "$VPC_ID" --query GroupId --output text)
fi
echo "Runner SG: $SG_ID"

# 3) ⚠️ prod 網路變更（唯一敏感步驟）：允許 runner SG 連 DB SG 的 5432。
#    需要對 replica 的 DB SG 加一條 inbound。可逆：見 README 的還原指令。
echo "Authorizing ${SG_ID} -> ${DB_SG}:5432 (replica) ..."
aws ec2 authorize-security-group-ingress --region "$REGION" --group-id "$DB_SG" \
  --protocol tcp --port 5432 --source-group "$SG_ID" 2>/dev/null \
  && echo "  added" || echo "  (already present or insufficient perms — see README)"

# 4) CodeBuild 專案（掛 VPC）
ENV_JSON=$(cat <<JSON
{"type":"LINUX_CONTAINER","image":"aws/codebuild/amazonlinux2-x86_64-standard:5.0","computeType":"BUILD_GENERAL1_SMALL"}
JSON
)
VPC_JSON=$(cat <<JSON
{"vpcId":"${VPC_ID}","subnets":["${SUBNETS//,/\",\"}"],"securityGroupIds":["${SG_ID}"]}
JSON
)
SOURCE_JSON=$(cat <<JSON
{"type":"GITHUB","location":"${SOURCE_REPO}","buildspec":"infra/vpc-runner/buildspec.yml"}
JSON
)
if aws codebuild batch-get-projects --region "$REGION" --names "$PROJECT" \
     --query 'projects[0].name' --output text 2>/dev/null | grep -q "$PROJECT"; then
  echo "Updating CodeBuild project $PROJECT ..."
  aws codebuild update-project --region "$REGION" --name "$PROJECT" \
    --service-role "$ROLE_ARN" --environment "$ENV_JSON" --vpc-config "$VPC_JSON" \
    --source "$SOURCE_JSON" --artifacts '{"type":"NO_ARTIFACTS"}' --timeout-in-minutes 60 >/dev/null
else
  echo "Creating CodeBuild project $PROJECT ..."
  aws codebuild create-project --region "$REGION" --name "$PROJECT" \
    --service-role "$ROLE_ARN" --environment "$ENV_JSON" --vpc-config "$VPC_JSON" \
    --source "$SOURCE_JSON" --artifacts '{"type":"NO_ARTIFACTS"}' --timeout-in-minutes 60 >/dev/null
fi

echo
echo "Done. 觸發範例（軸一 D ring 偵測）："
echo "  aws codebuild start-build --region ${REGION} --project-name ${PROJECT} \\"
echo "    --environment-variables-override name=JOB,value=ring"
echo "其餘 JOB=acceptance（conformal 驗收）/ JOB=l1（訓練樣本匯出）"
echo "注意：private subnet 需有 NAT egress 才能 pip install + 呼叫 staging endpoint；replica 連線走 VPC 內網。"
