#!/usr/bin/env bash
set -euo pipefail

ENV_FILE="${ENV_FILE:-}"
if [ -z "$ENV_FILE" ]; then
  if [ -f /srv/devagent/.env ]; then
    ENV_FILE="/srv/devagent/.env"
  elif [ -f .env ]; then
    ENV_FILE=".env"
  else
    echo "No env file found. Set ENV_FILE=/path/to/.env" >&2
    exit 1
  fi
fi

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

JOB_ID="${1:-$(date +%s)}"
REPO="${2:-devagent-live-test}"
REQUESTED_BY="${3:-${DEVAGENT_ALLOWED_USERS%%,*}}"
COMMAND="${4:-echo matrix-job-ok && sleep 2}"
MODE="${5:-text}"

if [ -z "${REQUESTED_BY}" ]; then
  echo "REQUESTED_BY ist leer (DEVAGENT_ALLOWED_USERS fehlt?)" >&2
  exit 2
fi

echo "[1/4] room info"
python3 scripts/matrix_room_info.py

echo "[2/4] send jobcard job_id=${JOB_ID}"
SEND_OUT="$(python3 scripts/matrix_send_jobcard.py \
  --job-id "$JOB_ID" \
  --repo "$REPO" \
  --branch main \
  --command "$COMMAND" \
  --requested-by "$REQUESTED_BY" \
  --mode "$MODE")"
echo "$SEND_OUT"

EVENT_ID="$(python3 -c 'import json,sys; data=json.loads(sys.argv[1]);
for k in ("text","notice","event"):
    if k in data and isinstance(data[k], dict) and "event_id" in data[k]:
        print(data[k]["event_id"]); break
' "$SEND_OUT")"

if [ -z "$EVENT_ID" ]; then
  echo "Konnte event_id nicht auslesen" >&2
  exit 3
fi

echo "[3/4] fetch sent event ${EVENT_ID}"
python3 scripts/matrix_get_event.py --event-id "$EVENT_ID"

echo "[4/4] next"
echo "Reagiere in Element auf die sichtbare JobCard mit ✅ oder 🛑"
echo "Audit: ${DEVAGENT_ARTIFACTS_ROOT:-/srv/agent-artifacts}/job-${JOB_ID}/audit.jsonl"
