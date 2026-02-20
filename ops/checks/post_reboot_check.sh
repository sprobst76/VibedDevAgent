#!/usr/bin/env bash
set -euo pipefail

SERVICE="${1:-devagent}"
ALLOWED_USER="${2:-@alice:example.org}"
ARTIFACTS_ROOT="${DEVAGENT_ARTIFACTS_ROOT:-/srv/agent-artifacts}"
JOB_ID="reboot-$(date +%s)"

echo "[1/5] service enabled"
systemctl is-enabled "$SERVICE"

echo "[2/5] service active"
systemctl is-active "$SERVICE"

echo "[3/5] last-boot logs"
journalctl -u "$SERVICE" -b --no-pager | tail -n 80

echo "[4/5] quick reaction smoke"
python3 -m core.main \
  --job-id "$JOB_ID" \
  --reaction '✅' \
  --user-id "$ALLOWED_USER" \
  --allowed-users "$ALLOWED_USER" \
  --artifacts-root "$ARTIFACTS_ROOT"

echo "[5/5] audit created"
test -f "${ARTIFACTS_ROOT}/job-${JOB_ID}/audit.jsonl"

echo "Post-reboot checks passed"
