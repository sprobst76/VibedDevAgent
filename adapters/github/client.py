"""GitHub Actions API client — pure stdlib, no new dependencies."""
from __future__ import annotations

import json
import re
import subprocess
from urllib import error as urllib_error
from urllib import request

# Matches both HTTPS and SSH GitHub remote URLs.
_REMOTE_RE = re.compile(r"github\.com[:/]([^/\s]+)/([^/\s.]+?)(?:\.git)?\s*$")


def detect_github_repo(local_path: str) -> tuple[str, str] | None:
    """Run `git remote get-url origin` in *local_path* and parse owner/repo.

    Returns ``(owner, repo)`` or ``None`` if the remote is not on GitHub or the
    command fails.
    """
    try:
        result = subprocess.run(
            ["git", "-C", local_path, "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            m = _REMOTE_RE.search(result.stdout.strip())
            if m:
                return m.group(1), m.group(2)
    except Exception:  # noqa: BLE001
        pass
    return None


def fetch_workflow_runs(
    owner: str,
    repo: str,
    token: str = "",
    per_page: int = 10,
    timeout: float = 10.0,
) -> list[dict]:
    """Return the latest workflow runs for *owner*/*repo* from the GitHub API.

    *token* is optional. Without a token the GitHub API works for public repos
    but is rate-limited to 60 requests/hour. With a token (PAT with
    ``read:actions``), the limit is 5000 requests/hour.

    Returns an empty list on any error (network, auth, rate-limit, …).
    """
    url = (
        f"https://api.github.com/repos/{owner}/{repo}/actions/runs"
        f"?per_page={per_page}"
    )
    headers: dict[str, str] = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "devagent/0.1",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = request.Request(url, headers=headers)
    try:
        with request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            data = json.loads(resp.read().decode("utf-8"))
            return data.get("workflow_runs", [])
    except Exception:  # noqa: BLE001
        return []


def latest_per_workflow(runs: list[dict]) -> dict[str, dict]:
    """Return ``{workflow_name: most_recent_run}`` — the GitHub API already
    returns runs sorted newest-first, so the first occurrence of each workflow
    name is the most recent.
    """
    seen: dict[str, dict] = {}
    for run in runs:
        name = run.get("name") or str(run.get("workflow_id", "unknown"))
        if name not in seen:
            seen[name] = run
    return seen


def run_conclusion(run: dict) -> str:
    """Return a normalised conclusion string for a single workflow run.

    Active runs (queued / in_progress / waiting / …) → ``"in_progress"``.
    Completed runs → ``run["conclusion"]`` (success / failure / cancelled / …).
    """
    status = run.get("status", "unknown")
    if status in ("queued", "in_progress", "waiting", "requested", "pending"):
        return "in_progress"
    return run.get("conclusion") or "unknown"


def overall_conclusion(by_workflow: dict[str, dict]) -> str:
    """Aggregate conclusion across all workflows.

    * ``"failure"`` — at least one workflow failed or timed out.
    * ``"in_progress"`` — at least one workflow is still running (and none failed).
    * ``"success"`` — all workflows completed successfully.
    * ``"unknown"`` — *by_workflow* is empty.
    """
    if not by_workflow:
        return "unknown"
    conclusions = {run_conclusion(r) for r in by_workflow.values()}
    if conclusions & {"failure", "timed_out", "startup_failure"}:
        return "failure"
    if "in_progress" in conclusions:
        return "in_progress"
    if conclusions <= {"success", "skipped", "neutral"}:
        return "success"
    return "unknown"
