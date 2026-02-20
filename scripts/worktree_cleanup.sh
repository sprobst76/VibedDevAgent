#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -ne 2 ]; then
  echo "Usage: $0 <repo> <job_id>" >&2
  exit 1
fi

REPO="$1"
JOB_ID="$2"

REPOS_ROOT="${DEVAGENT_REPOS_ROOT:-/srv/repos}"
WORKTREES_ROOT="${DEVAGENT_WORKTREES_ROOT:-/srv/agent-worktrees}"
WT_PATH="${WORKTREES_ROOT}/${REPO}/job-${JOB_ID}"

if [ -d "$WT_PATH" ]; then
  git -C "${REPOS_ROOT}/${REPO}" worktree remove "$WT_PATH" --force
  echo "Removed worktree: ${WT_PATH}"
else
  echo "Worktree already absent: ${WT_PATH}"
fi
