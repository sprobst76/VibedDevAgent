#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -lt 2 ] || [ "$#" -gt 3 ]; then
  echo "Usage: $0 <repo> <job_id> [base_branch]" >&2
  exit 1
fi

REPO="$1"
JOB_ID="$2"
BASE_BRANCH="${3:-main}"

REPOS_ROOT="${DEVAGENT_REPOS_ROOT:-/srv/repos}"
WORKTREES_ROOT="${DEVAGENT_WORKTREES_ROOT:-/srv/agent-worktrees}"
ARTIFACTS_ROOT="${DEVAGENT_ARTIFACTS_ROOT:-/srv/agent-artifacts}"

REPO_ROOT="${REPOS_ROOT}/${REPO}"
WT_ROOT="${WORKTREES_ROOT}/${REPO}"
WT_PATH="${WT_ROOT}/job-${JOB_ID}"
ARTIFACT_DIR="${ARTIFACTS_ROOT}/job-${JOB_ID}"

if [ ! -d "$REPO_ROOT/.git" ] && [ ! -f "$REPO_ROOT/.git" ]; then
  echo "Repository not found or not a git checkout: ${REPO_ROOT}" >&2
  exit 2
fi

mkdir -p "$WT_ROOT" "$ARTIFACT_DIR"

if [ -e "$WT_PATH" ]; then
  echo "Worktree path already exists: ${WT_PATH}" >&2
  exit 3
fi

# Detached checkout avoids branch-in-use conflicts with the main checkout.
git -C "$REPO_ROOT" worktree add --detach "$WT_PATH" "$BASE_BRANCH"

echo "$WT_PATH"
