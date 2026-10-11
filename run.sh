#!/usr/bin/env bash
# One command to a ready demo stack: backend :8000 + frontend :5173.
# Idempotent: frees the ports first. Prints READY only if the API answers with real archive data.
set -euo pipefail
root="$(cd "$(dirname "$0")" && pwd)"
for port in 8000 5173; do lsof -ti tcp:$port | xargs kill 2>/dev/null || true; done

[ -x "$root/backend/.venv/bin/python" ] || { python3 -m venv "$root/backend/.venv"; "$root/backend/.venv/bin/pip" install -q -r "$root/backend/requirements.txt"; }
[ -d "$root/frontend/node_modules" ] || (cd "$root/frontend" && npm install --silent)

# AWS_PROFILE from backend/.env (written by infra/local-live.sh) is for boto3, which
# reads it from the environment, not from pydantic settings.
aws_profile="$(grep -E '^AWS_PROFILE=' "$root/backend/.env" 2>/dev/null | tail -n1 | cut -d= -f2- || true)"
(cd "$root/backend" && exec env ${aws_profile:+AWS_PROFILE=$aws_profile} LOCAL_PERSISTENCE=true .venv/bin/uvicorn src.main:app --port 8000 --log-level warning) > "$root/backend/server.log" 2>&1 &
(cd "$root/frontend" && exec npx vite --port 5173 --strictPort) > "$root/frontend/vite.log" 2>&1 &

for _ in $(seq 1 60); do
  if curl -sf localhost:8000/api/health >/dev/null && curl -sf localhost:5173 >/dev/null; then break; fi
  sleep 0.5
done
frames=$(curl -sf "localhost:8000/api/system" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['archive']['lakes'].get('south_lhonak',0), '|', d['sns']['label'])") \
  || { echo "NOT READY: backend not answering (see backend/server.log)"; exit 1; }
echo "archive frames | SNS: $frames"
[ "${frames%% *}" -gt 0 ] || { echo "NOT READY: no archive. Build it: cd backend && .venv/bin/python -m scripts.build_archive"; exit 1; }
echo "READY  ->  http://localhost:5173"
