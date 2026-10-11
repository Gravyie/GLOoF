#!/usr/bin/env bash
# Tears down EVERYTHING this stack created in BOTH accounts (main + SMS).
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
tf="terraform -chdir=$here/terraform"
echo "This destroys the GLOF stack in the main AND the SMS account:"
echo "  site, API, Lambda, archive bucket (with all archived outlines), alert log table, SNS topic and subscriptions."
echo "Pending (unconfirmed) email subscriptions cannot be deleted by Terraform; SNS removes them after 3 days."
read -r -p "Type 'destroy glof' to continue: " ok
[ "$ok" = "destroy glof" ] || { echo "aborted"; exit 1; }
$tf init -input=false -reconfigure -backend-config=backend.hcl >/dev/null
$tf destroy -input=false
echo "Done. The state bucket (bootstrap-state.sh) is kept; delete it by hand if you want."
