#!/usr/bin/env bash
# One-time IAM setup so GitHub Actions can deploy via OIDC (no stored AWS keys).
# Run this with AWS admin credentials. It is idempotent-ish: re-running after a
# partial setup is safe (it skips the provider if it already exists).
#
#   bash infra/oidc/setup.sh
#
# Afterwards, set the repo VARIABLE (not a secret):
#   Settings -> Secrets and variables -> Actions -> Variables -> New repository variable
#     AWS_DEPLOY_ROLE_ARN = <printed role ARN>
# or:  gh variable set AWS_DEPLOY_ROLE_ARN --repo thematters/spam-detection-scaffold --body <arn>
set -euo pipefail

ACCOUNT_ID=903380195283
ROLE_NAME=github-actions-spam-deploy
HERE="$(cd "$(dirname "$0")" && pwd)"

PROVIDER_ARN="arn:aws:iam::${ACCOUNT_ID}:oidc-provider/token.actions.githubusercontent.com"

# 1. OIDC provider (GitHub). Thumbprints are no longer validated by AWS for this
#    provider, but the API still requires the field; these are the published ones.
if aws iam get-open-id-connect-provider --open-id-connect-provider-arn "$PROVIDER_ARN" >/dev/null 2>&1; then
  echo "OIDC provider already exists: $PROVIDER_ARN"
else
  echo "Creating GitHub OIDC provider ..."
  aws iam create-open-id-connect-provider \
    --url "https://token.actions.githubusercontent.com" \
    --client-id-list "sts.amazonaws.com" \
    --thumbprint-list 1b511abead59c6ce207077c0bf0e0043b1382612 6938fd4d98bab03faadb97b34396831e3780aea1
fi

# 2. Deploy role with the GitHub trust policy.
if aws iam get-role --role-name "$ROLE_NAME" >/dev/null 2>&1; then
  echo "Role $ROLE_NAME exists; updating trust policy ..."
  aws iam update-assume-role-policy --role-name "$ROLE_NAME" \
    --policy-document "file://${HERE}/github-trust-policy.json"
else
  echo "Creating role $ROLE_NAME ..."
  aws iam create-role --role-name "$ROLE_NAME" \
    --assume-role-policy-document "file://${HERE}/github-trust-policy.json" \
    --description "GitHub Actions OIDC deploy role for spam-comment-model-service"
fi

# 3. Inline least-privilege deploy permissions.
aws iam put-role-policy --role-name "$ROLE_NAME" \
  --policy-name spam-comment-deploy \
  --policy-document "file://${HERE}/deploy-permissions.json"

ROLE_ARN="$(aws iam get-role --role-name "$ROLE_NAME" --query Role.Arn --output text)"
echo
echo "Done. Role ARN:"
echo "  $ROLE_ARN"
echo
echo "Now set the repo variable:"
echo "  gh variable set AWS_DEPLOY_ROLE_ARN --repo thematters/spam-detection-scaffold --body \"$ROLE_ARN\""
