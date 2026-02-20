#!/usr/bin/env bash
set -euo pipefail

ARTIFACTS_ROOT="${1:-/tmp/devagent-smoke-artifacts}"
JOB_ID="00099"
USER_ID="@alice:example.org"
ALLOWED_USERS="@alice:example.org,@bob:example.org"

rm -rf "$ARTIFACTS_ROOT"
mkdir -p "$ARTIFACTS_ROOT"

echo "[1/3] run approval reaction"
python3 -m core.main \
  --job-id "$JOB_ID" \
  --reaction "✅" \
  --user-id "$USER_ID" \
  --allowed-users "$ALLOWED_USERS" \
  --artifacts-root "$ARTIFACTS_ROOT"

echo "[2/3] verify audit log"
AUDIT_FILE="$ARTIFACTS_ROOT/job-${JOB_ID}/audit.jsonl"
test -f "$AUDIT_FILE"
grep -q '"action": "approve"' "$AUDIT_FILE"
grep -q '"allowed": true' "$AUDIT_FILE"

echo "[3/3] run unit tests"
python3 -m unittest discover -s tests -p 'test_*.py'

echo "MVP smoke passed"
