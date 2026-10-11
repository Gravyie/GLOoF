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

## If CloudFront is blocked (new accounts)

A brand-new AWS account can fail at the CloudFront step with *"Your account must be verified before you can add new
CloudFront resources"*. That is an account check by AWS, not a problem in the stack. Two things to do:

1. **Deploy without CloudFront now.** Set `enable_cloudfront = false` in `terraform/terraform.tfvars`, or answer No in
   `setup.sh`, and re-run `./deploy.sh`.
   - The frontend is bundled into the API image and served by the same task through the load balancer.
   - The site URL becomes `http://<alb-dns>`. It is HTTP only; with no domain there is no certificate.
   - The load balancer then accepts HTTP from anywhere instead of only from CloudFront.
2. **Open an AWS Support case** (Account and billing, account verification for CloudFront) quoting the error. Once
   verified, set `enable_cloudfront = true` and re-run `./deploy.sh` to get CloudFront and HTTPS.

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

The real SMS is triggered by the **History** (10-year) replay. When the playhead reaches **Warning**, the highest level
the real South Lhonak record reaches (24 Oct 2023, the first clear image after the outburst), one drill SMS goes to every
registered phone automatically, with no approval step. Flood night stays practice only.

1. **Deploy** and open the site URL.
2. **Confirm** the email subscriptions from each inbox.
3. **Turn the drill on:** in `terraform.tfvars` set `alert_dry_run = false`, `sim_live_sms = true`,
   `demo_sms_live = true`, then re-run `./deploy.sh` (or answer yes at stage 5 of `./setup.sh`). The Alerts panel then
   reads "Live alert via SNS (account ...XXXX, us-east-1)".
4. **Optional test:** in Flood night, enter the operator key and press **Send test** to send one `DRILL - TEST ONLY`
   message. Get the key with
   `aws secretsmanager get-secret-value --secret-id glof/alert-api-key --query SecretString --output text --profile main`.
5. **Press Play in History.** When the replay reaches Warning in October 2023, the phones get the SMS, a notice reads
   "real SMS accepted by Amazon SNS", and the Alerts panel shows the MessageId and time.
6. **Rules that keep it to one message:**
   - one SMS per run;
   - a new run (Back to start, then Play) can send again, but only after a 5-minute cooldown
     (`drill_cooldown_seconds`);
   - refreshing the page, seeking back and forth, or restarting the server mid-run never resends.
7. **Turn the gates off again** (step 3 reversed) after the demo.

The site is public, so while the drill is on, anyone who presses Play can trigger the SMS (limited by the per-run rule,
the cooldown and the monthly spend cap). Turn it off when the demo is over.

What the messages look like:

- **SMS** (98 GSM-7 characters):
  `DRILL - TEST ONLY: WARNING - South Lhonak Lake` / `Keep away from the river. Follow official guidance.`
- **Email:** prefixed `DRILL - TEST ONLY`, with the full English and Hindi warning message.

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
