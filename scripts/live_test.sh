#!/usr/bin/env bash
set -euo pipefail

REPO_NAME="${1:-devagent-live-test}"
ALLOWED_USER="${2:-@alice:example.org}"
JOB_ID="${3:-$(date +%s)}"

REPOS_ROOT="${DEVAGENT_REPOS_ROOT:-/srv/repos}"
WORKTREES_ROOT="${DEVAGENT_WORKTREES_ROOT:-/srv/agent-worktrees}"
ARTIFACTS_ROOT="${DEVAGENT_ARTIFACTS_ROOT:-/srv/agent-artifacts}"

REPO_PATH="${REPOS_ROOT}/${REPO_NAME}"
AUDIT_FILE="${ARTIFACTS_ROOT}/job-${JOB_ID}/audit.jsonl"
RUNNER_LOG="${ARTIFACTS_ROOT}/job-${JOB_ID}/runner.log"

echo "[0/6] preflight"
command -v python3 >/dev/null
command -v git >/dev/null
command -v tmux >/dev/null
mkdir -p "$REPOS_ROOT" "$WORKTREES_ROOT" "$ARTIFACTS_ROOT"

echo "[1/6] prepare repo ${REPO_PATH}"
if [ ! -d "$REPO_PATH/.git" ] && [ ! -f "$REPO_PATH/.git" ]; then
  mkdir -p "$REPO_PATH"
  git -C "$REPO_PATH" init
  git -C "$REPO_PATH" config user.email "devagent@example.local"
  git -C "$REPO_PATH" config user.name "DevAgent"
  echo "# ${REPO_NAME}" > "$REPO_PATH/README.md"
  git -C "$REPO_PATH" add README.md
  git -C "$REPO_PATH" commit -m "init"
  git -C "$REPO_PATH" branch -M main
  git -C "$REPO_PATH" update-ref refs/remotes/origin/main HEAD
fi

echo "[2/6] worktree create/diff/cleanup"
scripts/worktree_create.sh "$REPO_NAME" "$JOB_ID" main >/dev/null
scripts/worktree_diff.sh "$REPO_NAME" "$JOB_ID" origin/main >/dev/null
scripts/worktree_cleanup.sh "$REPO_NAME" "$JOB_ID" >/dev/null

echo "[3/6] approval -> runner start"
OUTPUT="$(python3 -m core.main \
  --job-id "$JOB_ID" \
  --reaction '✅' \
  --user-id "$ALLOWED_USER" \
  --allowed-users "$ALLOWED_USER" \
  --run-command 'echo live-runner-ok && sleep 1' \
  --run-cwd "$REPO_PATH" \
  --artifacts-root "$ARTIFACTS_ROOT")"
echo "$OUTPUT"
echo "$OUTPUT" | grep -q 'accepted=True'
echo "$OUTPUT" | grep -q 'state=RUNNING'

echo "[4/6] verify audit + runner log"
test -f "$AUDIT_FILE"
grep -q '"action": "approve"' "$AUDIT_FILE"
grep -q '"action": "runner_start"' "$AUDIT_FILE"
sleep 1
if [ -f "$RUNNER_LOG" ]; then
  grep -q 'live-runner-ok' "$RUNNER_LOG"
fi

echo "[5/6] generate ui data"
python3 ui/generate_dashboard_data.py

echo "[6/6] done"
echo "Live test passed for job ${JOB_ID}"
