#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -lt 2 ] || [ "$#" -gt 3 ]; then
  echo "Usage: $0 <repo> <job_id> [base_ref]" >&2
  exit 1
fi

REPO="$1"
JOB_ID="$2"
BASE_REF="${3:-origin/main}"

WORKTREES_ROOT="${DEVAGENT_WORKTREES_ROOT:-/srv/agent-worktrees}"
ARTIFACTS_ROOT="${DEVAGENT_ARTIFACTS_ROOT:-/srv/agent-artifacts}"
WT_PATH="${WORKTREES_ROOT}/${REPO}/job-${JOB_ID}"
OUT_FILE="${ARTIFACTS_ROOT}/job-${JOB_ID}/diff.patch"

if [ ! -d "$WT_PATH" ]; then
  echo "Worktree not found: ${WT_PATH}" >&2
  exit 2
fi

mkdir -p "$(dirname "$OUT_FILE")"

git -C "$WT_PATH" diff "$BASE_REF" > "$OUT_FILE"

echo "$OUT_FILE"
