# OIDC deploy setup

GitHub Actions deploys the comment-model Lambda via **OIDC** — it assumes an AWS
role using a short-lived token, so no long-lived AWS keys are stored in the repo.

## One-time (AWS admin runs this)

```bash
bash infra/oidc/setup.sh
```

This creates:
- the GitHub OIDC identity provider (`token.actions.githubusercontent.com`),
- an IAM role `github-actions-spam-deploy` trusting **only**
  `repo:thematters/spam-detection-scaffold:*` (tighten to `:ref:refs/heads/main`
  if you want to restrict to the main branch),
- a least-privilege inline policy (`deploy-permissions.json`) scoped to the
  `spam-comment-model-service` stack, its Lambda/API Gateway/ECR, `iam:PassRole`
  on the existing Lambda exec role, and the SAM bucket.

Then set the repo **variable** (not a secret — a role ARN is not sensitive):

```bash
gh variable set AWS_DEPLOY_ROLE_ARN \
  --repo thematters/spam-detection-scaffold \
  --body "arn:aws:iam::903380195283:role/github-actions-spam-deploy"
```

## Files

| File | Purpose |
| --- | --- |
| `setup.sh` | Idempotent-ish provisioning script. |
| `github-trust-policy.json` | Who may assume the role (this repo via OIDC). |
| `deploy-permissions.json` | What the role may do (least-privilege deploy). |

## Deploy

`Actions -> deploy-comment-model -> Run workflow`, with
`model_s3_uri = s3://aws-sam-cli-managed-default-samclisourcebucket-8jnezd3nhz9w/comment-model/spam_comment_e5.tar`.
