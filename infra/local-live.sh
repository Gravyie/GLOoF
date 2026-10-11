#!/usr/bin/env bash
# Point the LOCAL app (./run.sh) at the real SNS topic, so the History replay sends the
# drill SMS from localhost too (e.g. to record a demo video).
#   cd infra && ./local-live.sh        # turn real SMS on for localhost
#   cd infra && ./local-live.sh off    # back to practice mode
# Writes backend/.env (gitignored). Publishes with the `sms` profile: that account owns
# the topic, so no cross-account permission is needed. Restart ./run.sh afterwards.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
env_file="$here/../backend/.env"
keys="SNS_TOPIC_ARN ALERT_DRY_RUN SIM_LIVE_SMS DEMO_SMS_LIVE AWS_PROFILE ALERT_API_KEY DRILL_COOLDOWN_SECONDS SES_SENDER SES_REGION SES_PROFILE ALERT_EMAILS"

strip() {  # remove our keys from backend/.env, keep anything else the user put there
  [ -f "$env_file" ] || return 0
  local tmp; tmp="$(mktemp)"
  grep -vE "^($(tr ' ' '|' <<<"$keys"))=" "$env_file" > "$tmp" || true
  mv "$tmp" "$env_file"
}

if [ "${1:-on}" = "off" ]; then
  strip
  echo "Local app back to practice mode. Restart: ./run.sh"
  exit 0
fi

topic="$(terraform -chdir="$here/terraform" output -raw alert_topic_arn 2>/dev/null || true)"
[ -n "$topic" ] || { echo "No alert_topic_arn in Terraform outputs. Deploy first (./setup.sh or ./deploy.sh)."; exit 1; }
aws sns get-topic-attributes --topic-arn "$topic" --profile sms --region us-east-1 >/dev/null \
  || { echo "The 'sms' profile cannot read $topic. Check ~/.aws/credentials."; exit 1; }
cooldown="${DRILL_COOLDOWN_SECONDS:-300}"
# HTML email via SES lives in the MAIN account: the local app uses the `main` profile for it.
ses_sender="$(terraform -chdir="$here/terraform" output -raw ses_sender 2>/dev/null || true)"
main_region="$(echo var.main_region | terraform -chdir="$here/terraform" console -var-file=terraform.tfvars 2>/dev/null | tr -d '"')"
emails="$(echo 'join(",", nonsensitive(var.alert_emails))' | terraform -chdir="$here/terraform" console -var-file=terraform.tfvars 2>/dev/null | tr -d '"')"
key="$(openssl rand -hex 16)"

strip
cat >> "$env_file" <<ENV
SNS_TOPIC_ARN=$topic
ALERT_DRY_RUN=false
SIM_LIVE_SMS=true
DEMO_SMS_LIVE=true
AWS_PROFILE=sms
ALERT_API_KEY=$key
DRILL_COOLDOWN_SECONDS=$cooldown
ENV
if [ -n "$ses_sender" ]; then
  cat >> "$env_file" <<ENV
SES_SENDER=$ses_sender
SES_REGION=${main_region:-ap-south-1}
SES_PROFILE=main
ALERT_EMAILS=$emails
ENV
  echo "HTML alert email via Amazon SES (main account, ${main_region:-ap-south-1})."
fi
echo "Local app now sends REAL drill SMS when the History replay reaches Warning (once per run, ${cooldown}s cooldown)."
echo "Topic: $topic   Local operator key: $key"
echo "Restart the local app: cd .. && ./run.sh"
