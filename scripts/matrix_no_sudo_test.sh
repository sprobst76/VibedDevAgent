#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ENV_FILE:-$ROOT_DIR/.env}"
if [ ! -f "$ENV_FILE" ]; then
  echo "Env file not found: $ENV_FILE" >&2
  echo "Set ENV_FILE=/path/to/.env" >&2
  exit 1
fi

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

: "${MATRIX_HOMESERVER_URL:?Missing MATRIX_HOMESERVER_URL in .env}"
: "${MATRIX_ACCESS_TOKEN:?Missing MATRIX_ACCESS_TOKEN in .env}"
: "${MATRIX_ROOM_ID:?Missing MATRIX_ROOM_ID in .env}"
: "${DEVAGENT_ALLOWED_USERS:?Missing DEVAGENT_ALLOWED_USERS in .env}"

JOB_ID="${1:-$(date +%s)}"
REPO_NAME="${2:-devagent-live-test}"
REQUESTED_BY="${3:-${DEVAGENT_ALLOWED_USERS%%,*}}"
TEST_CMD="${4:-echo matrix-job-ok && sleep 1}"

TEST_ROOT="/tmp/devagent-matrix-autotest-${JOB_ID}"
export DEVAGENT_REPOS_ROOT="$TEST_ROOT/repos"
export DEVAGENT_WORKTREES_ROOT="$TEST_ROOT/worktrees"
export DEVAGENT_ARTIFACTS_ROOT="$TEST_ROOT/artifacts"
export DEVAGENT_MATRIX_STATE_FILE="$TEST_ROOT/state/matrix_worker_state.json"
export DEVAGENT_MATRIX_SEND_NOTICES="0"

mkdir -p "$DEVAGENT_REPOS_ROOT" "$DEVAGENT_WORKTREES_ROOT" "$DEVAGENT_ARTIFACTS_ROOT" "$(dirname "$DEVAGENT_MATRIX_STATE_FILE")"

REPO_PATH="$DEVAGENT_REPOS_ROOT/$REPO_NAME"
if [ ! -d "$REPO_PATH/.git" ] && [ ! -f "$REPO_PATH/.git" ]; then
  mkdir -p "$REPO_PATH"
  git -C "$REPO_PATH" init
  git -C "$REPO_PATH" config user.email "devagent@example.local"
  git -C "$REPO_PATH" config user.name "DevAgent"
  echo "# $REPO_NAME" > "$REPO_PATH/README.md"
  git -C "$REPO_PATH" add README.md
  git -C "$REPO_PATH" commit -m "init"
  git -C "$REPO_PATH" branch -M main
  git -C "$REPO_PATH" update-ref refs/remotes/origin/main HEAD
fi

cd "$ROOT_DIR"

echo "[1/8] room info"
python3 scripts/matrix_room_info.py >/tmp/devagent-room-info-${JOB_ID}.json
if grep -q '"algorithm"' "/tmp/devagent-room-info-${JOB_ID}.json"; then
  echo "WARN: Room appears to be encrypted (m.room.encryption found)." >&2
  echo "      This raw API client sends unencrypted m.room.message and Element may hide it." >&2
fi

# Pre-sync: get current batch token so the worker only processes events sent AFTER this point.
echo "[1b/8] pre-sync to anchor since token"
python3 -c "
import json, os, sys
sys.path.insert(0, '.')
from adapters.matrix.client import MatrixClient
client = MatrixClient(os.environ['MATRIX_HOMESERVER_URL'], os.environ['MATRIX_ACCESS_TOKEN'])
r = client.sync(since=None, timeout_ms=5000)
state_file = os.environ.get('DEVAGENT_MATRIX_STATE_FILE', '')
if state_file:
    import pathlib
    pathlib.Path(state_file).parent.mkdir(parents=True, exist_ok=True)
    pathlib.Path(state_file).write_text(json.dumps({'since': r.next_batch, 'jobcards': {}, 'job_states': {}}))
    print(f'since anchored: {r.next_batch[:40]}...')
"

echo "[2/8] send visible jobcard"
SEND_OUT=""
for attempt in 1 2 3; do
  set +e
  SEND_OUT="$(python3 scripts/matrix_send_jobcard.py \
    --job-id "$JOB_ID" \
    --repo "$REPO_NAME" \
    --branch main \
    --command "$TEST_CMD" \
    --requested-by "$REQUESTED_BY" \
    --mode text 2>&1)"
  rc=$?
  set -e
  if [ "$rc" -eq 0 ]; then
    break
  fi
  echo "send failed (attempt ${attempt}/3): $SEND_OUT" >&2
  sleep 2
done
if [ -z "$SEND_OUT" ] || ! echo "$SEND_OUT" | grep -q '"event_id"'; then
  echo "Unable to send jobcard after retries. Check network/DNS and Matrix credentials." >&2
  exit 4
fi
echo "$SEND_OUT"

EVENT_ID="$(python3 -c 'import json,sys; d=json.loads(sys.argv[1]);
for k in ("text","notice","event"):
    if k in d and isinstance(d[k], dict) and "event_id" in d[k]:
        print(d[k]["event_id"]); break
' "$SEND_OUT")"
if [ -z "$EVENT_ID" ]; then
  echo "No event_id extracted" >&2
  exit 2
fi

echo "[3/8] fetch sent event"
python3 scripts/matrix_get_event.py --event-id "$EVENT_ID" >/tmp/devagent-event-${JOB_ID}.json

echo "[4/8] worker once -> should create job + worktree"
python3 -m core.matrix_worker --once

echo "[5/8] approve via reaction ✅"
python3 scripts/matrix_react.py --event-id "$EVENT_ID" --key "✅"
python3 -m core.matrix_worker --once

echo "[6/8] stop via reaction 🛑"
python3 scripts/matrix_react.py --event-id "$EVENT_ID" --key "🛑"
python3 -m core.matrix_worker --once

AUDIT_FILE="$DEVAGENT_ARTIFACTS_ROOT/job-${JOB_ID}/audit.jsonl"
RUNNER_LOG="$DEVAGENT_ARTIFACTS_ROOT/job-${JOB_ID}/runner.log"

echo "[7/8] verify artifacts"
test -f "$AUDIT_FILE"
grep -q '"action": "job_created"' "$AUDIT_FILE"
grep -q '"action": "approve"' "$AUDIT_FILE"
grep -q '"action": "runner_start"' "$AUDIT_FILE"
grep -q '"action": "stop"' "$AUDIT_FILE"

if [ -f "$RUNNER_LOG" ]; then
  echo "Runner log tail:"
  tail -n 20 "$RUNNER_LOG"
fi

echo "[8/8] done"
echo "SUCCESS job_id=$JOB_ID"
echo "audit=$AUDIT_FILE"
echo "event=$EVENT_ID"
echo "room_info=/tmp/devagent-room-info-${JOB_ID}.json"
echo "event_dump=/tmp/devagent-event-${JOB_ID}.json"
