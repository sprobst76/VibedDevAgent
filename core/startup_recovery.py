"""Startup recovery utilities for stale worktree cleanup."""

from __future__ import annotations

import shutil
from pathlib import Path


def recover_stale_worktrees(worktrees_root: str, active_job_ids: set[str]) -> list[str]:
    """Remove stale job worktrees and return removed paths."""
    root = Path(worktrees_root)
    if not root.exists():
        return []

    removed: list[str] = []
    for repo_dir in root.iterdir():
        if not repo_dir.is_dir():
            continue

        for job_dir in repo_dir.glob("job-*"):
            if not job_dir.is_dir():
                continue

            job_id = job_dir.name.removeprefix("job-")
            if job_id in active_job_ids:
                continue

            shutil.rmtree(job_dir)
            removed.append(str(job_dir))

    return removed
