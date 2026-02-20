"""Wrapper around worktree helper scripts."""

from __future__ import annotations

import subprocess
from pathlib import Path


class WorktreeManager:
    def __init__(self, scripts_dir: str = "scripts") -> None:
        self.scripts_dir = Path(scripts_dir)

    def _run_script(self, script_name: str, *args: str) -> str:
        script = self.scripts_dir / script_name
        result = subprocess.run(
            [str(script), *args],
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout.strip()

    def create(self, repo: str, job_id: str, base_branch: str = "main") -> str:
        return self._run_script("worktree_create.sh", repo, job_id, base_branch)

    def diff(self, repo: str, job_id: str, base_ref: str = "origin/main") -> str:
        return self._run_script("worktree_diff.sh", repo, job_id, base_ref)

    def cleanup(self, repo: str, job_id: str) -> str:
        return self._run_script("worktree_cleanup.sh", repo, job_id)
