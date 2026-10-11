# Deploying Glacial Lake Monitor to AWS

The stack spans **two AWS accounts**, deployed by **one Terraform stack**.

| Account | Region | Holds |
|---|---|---|
| **SMS account** | us-east-1 (fixed) | The SNS alert topic, its policy, SMS and email subscriptions, SMS preferences. Nothing else. |
| **Main account** | `main_region` (e.g. ap-south-1) | Everything else: S3 (archive, website), ECR, Lambda (ingest, alarm relay), EventBridge, ECS Fargate + ALB, CloudFront, DynamoDB, Secrets Manager, CloudWatch alarms, Terraform state. |

## Why two accounts

SMS delivery from SNS starts in a per-account, per-region **sandbox**: it reaches only numbers verified in that
account and region. The demo phones are already verified in a separate account in us-east-1.

Keeping that account to SNS alone keeps its blast radius tiny. Its credentials can do one thing, and the compute that
publishes lives elsewhere. The main account's roles publish to the topic across accounts. The topic policy allows only
three of them (ingest Lambda, API task, alarm relay), and each role is allowed `sns:Publish` on that one topic ARN only.

## Architecture

```
SMS account (us-east-1)                    Main account (main_region)
+---------------------------+              +-------------------------------------------------------------+
| SNS topic glof-alerts     | <--Publish-- | Lambda glof-ingest (daily, EventBridge) -> S3 archive       |
|  - 5 SMS subscriptions    | <--Publish-- | ECS Fargate API (1 task) <- ALB <- CloudFront /api/*        |
|  - email subscriptions    | <--Publish-- | Lambda glof-alarm-relay <- EventBridge <- CloudWatch alarms |
|  - SMS prefs: Transactional|             | CloudFront -> S3 website (Origin Access Control)            |
+---------------------------+              | DynamoDB glof-alerts (audit log + demo latch)                |
                                           +-------------------------------------------------------------+
```

CloudWatch alarms reach the topic through a small relay Lambda rather than a direct alarm action. The alarms live in
the main region and the topic in us-east-1, and as far as we know an alarm's SNS action must be in its own region
(not confirmed in AWS docs). The relay works either way.

## Quickest path: one script

```bash
cd infra && ./setup.sh
```

Paste the access key of each account when asked (input hidden; keys go only to `~/.aws/credentials`).
The script then does everything below automatically:
- checks both accounts;
- reads the verified sandbox numbers;
- asks for alert emails and whether to turn the real SMS drill on;
- writes `terraform.tfvars`;
- creates the state bucket;
- starts Docker;
- after one confirmation, runs `deploy.sh`.

It ends by printing the site URL and the operator key. Re-running it is safe and remembers your answers.

The sections below describe the same steps by hand.

## One-time setup

### 1. Credentials (both accounts, static keys, never root)

- **SMS account.** In the IAM console, create a user (e.g. `glof-sns-deployer`) with an inline policy allowing `sns:*`
  and `sts:GetCallerIdentity`. If you want `enable_sms_delivery_logs = true`, also allow `iam:CreateRole`,
  `iam:PutRolePolicy`, `iam:PassRole`, `iam:GetRole`, `iam:DeleteRole` and `iam:DeleteRolePolicy` on
  `arn:aws:iam::<sms-account>:role/glof-alerts-sms-delivery-logs`. Create an access key for that user.
- **Main account.** An IAM user (or SSO profile) with administrator rights for the deploy.

Add both to `~/.aws/credentials` (never commit this file):

```ini
[main]
aws_access_key_id     = AKIA...
aws_secret_access_key = ...

[sms]
aws_access_key_id     = AKIA...
aws_secret_access_key = ...
```

and to `~/.aws/config`:

```ini
[profile main]
region = ap-south-1

[profile sms]
region = us-east-1
```

Check that both answer and that they are different accounts:

```bash
aws sts get-caller-identity --profile main
aws sts get-caller-identity --profile sms
```

### 2. Terraform remote state (main account, once)

```bash
cd infra && ./bootstrap-state.sh main ap-south-1
```

This creates a versioned, encrypted, private bucket `glof-tfstate-<main-account>` and writes `terraform/backend.hcl`
(gitignored). Locking uses S3 lock files (`use_lockfile = true`, Terraform 1.10 or later), so no DynamoDB lock table
is needed.

### 3. Variables

```bash
cp terraform/terraform.tfvars.example terraform/terraform.tfvars   # gitignored
```

Fill in account IDs, profile names, the verified phone numbers (E.164) and the email addresses. The three demo gates
(`alert_dry_run`, `sim_live_sms`, `demo_sms_live`) default to safe values; flip all three only for the drill.

## Deploy

```bash
cd infra && ./deploy.sh
```

**Preflight** (aborts with a clear message):
1. The `main` profile is `expected_main_account_id`.
2. The `sms` profile is `expected_sms_account_id`.
3. The two are different accounts.
4. Every number in `demo_sms_numbers` is `Verified` in the SMS sandbox (SMS account, us-east-1).

Docker must be running.

**Steps:**
1. Build and push the Lambda image.
2. Build and push the API image.
3. `terraform apply` for both accounts.
4. Build the frontend.
5. Sync it to the site bucket.
6. Invalidate CloudFront.
7. Seed the archive to S3.
8. Print the site URL, the topic ARN, and how to read the operator key.

**Destroy:** `./destroy.sh` (asks you to type `destroy glof`).

## Manual steps Terraform cannot do

- **Verify the sandbox phone numbers** in the SNS console of the SMS account, region us-east-1 (Text messaging (SMS),
  Sandbox destination phone numbers). Already done for the demo phones; `deploy.sh` re-checks.
- **Confirm every email subscription.** SNS sends a confirmation link after `terraform apply`. Until it is clicked the
  subscription stays `PendingConfirmation`; Terraform cannot delete an unconfirmed subscription, and SNS removes it after
  3 days.
- **Spend limit.** `sms_spend_limit` (default USD 1) is the account's monthly SMS cap. Raising it above the account
  quota needs an AWS Support case.
- **Leaving the sandbox** (sending to unverified numbers) needs an AWS Support request, and for India transactional SMS
  also DLT registration. Without DLT, Indian carriers may drop messages that SNS accepted; this is why the UI says
  "accepted by SNS", not "delivered".

## Demo runbook: one real SMS drill

1. **Deploy** and open the site URL.
2. **Confirm** the email subscriptions from each inbox.
3. **Turn the drill on:** in `terraform.tfvars` set `alert_dry_run = false`, `sim_live_sms = true`,
   `demo_sms_live = true`, then re-run `./deploy.sh`. The Flood night panel then reads
   "Live alert via SNS (account ...XXXX, us-east-1)".
4. **Get the operator key:**
   `aws secretsmanager get-secret-value --secret-id glof/alert-api-key --query SecretString --output text --profile main`
5. **Send test.** In Flood night, enter the key and press **Send test**. One `DRILL - TEST ONLY` message goes out, and the
   panel shows its MessageId as "accepted by SNS".
6. **Re-arm.** Press **Re-arm drill** so the latch is armed for the real run.
7. **Run Flood night** with **auto-approve off**. At EVACUATE, the public alert waits for approval.
8. **Approve EVACUATE.** Exactly one drill publish goes out: the 5 phones get the short SMS and subscribers get the
   bilingual email. Refreshing, seeking, resetting or restarting will not send again; only **Re-arm drill** allows
   another.
9. **Turn the gates off again** (step 3 reversed) after the demo.

What the messages look like:

- **SMS** (114 GSM-7 characters):
  `DRILL - TEST ONLY: EVACUATE - South Lhonak Lake` / `Move to high ground away from the river. Follow official guidance.`
- **Email:** prefixed `DRILL - TEST ONLY`, with the full English and Hindi evacuation message.

## Fallback: Terraform cannot reach the SMS account

If the SMS account's IAM does not allow access from your machine, split the stack. Both roots reuse the same modules.

1. **Main account first** (its roles must exist before the topic policy can name them). The topic ARN is
   deterministic: `arn:aws:sns:us-east-1:<sms-account>:glof-alerts`.
   ```bash
   cd infra/main
   terraform init -backend-config=../terraform/backend.hcl
   terraform apply -var alert_topic_arn=arn:aws:sns:us-east-1:<sms-account>:glof-alerts
   ```
2. **SMS account**, from AWS CloudShell inside it (upload `infra/modules/sms` and `infra/sms`):
   ```bash
   cd infra/sms && terraform init && terraform apply -var main_account_id=<main-account> \
     -var 'demo_sms_numbers=["+91..."]' -var 'alert_emails=["you@example.com"]'
   ```
   The output `alert_topic_arn` should match the ARN used in step 1.
3. Push the images, frontend and archive as in `deploy.sh` steps 1, 2 and 4 to 7.

## Cost notes

- **ECS:** one Fargate task (1 vCPU, 2 GB, ARM64).
- **ALB:** one load balancer.
- **CloudFront:** one distribution.
- **Lambda / DynamoDB:** very small usage.
- **No NAT gateway:** tasks run in public subnets with inbound allowed from the ALB only.
- **SMS:** capped by `sms_spend_limit`.

Run `./destroy.sh` when the demo is over.
