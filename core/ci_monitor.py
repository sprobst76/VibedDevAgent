"""Background CI status monitor — polls GitHub Actions for all registered projects.

Runs as a daemon thread (analogous to JobWatchdog). Sends Matrix notifications
only when the overall build status *changes* (ok→fail or fail→ok), so it never
spams about a persistently broken build.
"""
from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Callable

from adapters.github.client import (
    detect_github_repo,
    fetch_workflow_runs,
    latest_per_workflow,
    overall_conclusion,
    run_conclusion,
)

log = logging.getLogger("devagent.ci_monitor")


class CIMonitor:
    """Periodically polls GitHub Actions for every registered project and
    posts a Matrix notice when the build status changes.
    """

    def __init__(
        self,
        *,
        github_token: str,
        projects_file: str,
        room_id_for_fn: Callable[[str], str | None],
        notify_fn: Callable[[str, str], None],
        check_interval: int = 300,
    ) -> None:
        self._token = github_token
        self._projects_file = projects_file
        self._room_id_for = room_id_for_fn
        self._notify = notify_fn
        self._interval = check_interval

        # "owner/repo" → overall_conclusion from previous poll
        self._prev: dict[str, str] = {}
        # local_path → (owner, repo) | None  (cached; git is run once per path)
        self._repo_cache: dict[str, tuple[str, str] | None] = {}

        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="ci-monitor"
        )

    def start(self) -> None:
        self._thread.start()
        log.info("CI monitor started (interval=%ds)", self._interval)

    def stop(self) -> None:
        self._stop.set()

    # ── internal ──────────────────────────────────────────────────────────────

    def _loop(self) -> None:
        while not self._stop.wait(self._interval):
            try:
                self._check_once()
            except Exception:
                log.exception("CI monitor _check_once failed")

    def _read_projects(self) -> dict:
        try:
            p = Path(self._projects_file)
            if p.exists():
                return json.loads(p.read_text(encoding="utf-8")).get("projects", {})
        except Exception:
            log.exception("CI monitor: failed to read projects file")
        return {}

    def _resolve_repo(self, local_path: str) -> tuple[str, str] | None:
        if local_path not in self._repo_cache:
            self._repo_cache[local_path] = detect_github_repo(local_path)
        return self._repo_cache[local_path]

    def _check_once(self) -> None:
        for proj_name, proj in self._read_projects().items():
            local_path = proj.get("local_path", "")
            if not local_path:
                continue
            repo_info = self._resolve_repo(local_path)
            if not repo_info:
                continue
            owner, repo_name = repo_info
            try:
                runs = fetch_workflow_runs(owner, repo_name, self._token)
            except Exception:
                log.debug("CI fetch failed for %s/%s", owner, repo_name)
                continue
            if not runs:
                continue

            by_wf = latest_per_workflow(runs)
            conclusion = overall_conclusion(by_wf)
            key = f"{owner}/{repo_name}"
            prev = self._prev.get(key)

            if prev is not None and prev != conclusion:
                room_id = self._room_id_for(proj_name)
                if room_id:
                    msg = _format_change_notice(proj_name, owner, repo_name, by_wf, conclusion)
                    try:
                        self._notify(room_id, msg)
                    except Exception:
                        log.exception("CI monitor: notify failed for %s", proj_name)

            self._prev[key] = conclusion

    # ── public (used by !ghstatus handler) ───────────────────────────────────

    def fetch_status_for_projects(self, projects: dict) -> list[dict]:
        """Synchronously fetch GitHub Actions status for the given *projects* dict.

        Each entry is ``{"name", "owner", "repo", "conclusion", "by_workflow"}``
        or ``{"name", "error"}`` if the repo could not be detected or the API call
        failed.
        """
        results: list[dict] = []
        for proj_name, proj in projects.items():
            local_path = proj.get("local_path", "")
            repo_info = self._resolve_repo(local_path) if local_path else None
            if not repo_info:
                results.append({"name": proj_name, "error": "kein GitHub-Remote erkannt"})
                continue
            owner, repo_name = repo_info
            runs = fetch_workflow_runs(owner, repo_name, self._token)
            by_wf = latest_per_workflow(runs) if runs else {}
            conclusion = overall_conclusion(by_wf)
            results.append(
                {
                    "name": proj_name,
                    "owner": owner,
                    "repo": repo_name,
                    "conclusion": conclusion,
                    "by_workflow": by_wf,
                }
            )
        return results


# ── formatting helpers ────────────────────────────────────────────────────────

def _format_change_notice(
    proj_name: str,
    owner: str,
    repo_name: str,
    by_wf: dict[str, dict],
    conclusion: str,
) -> str:
    icon = _icon(conclusion)
    lines = [f"{icon} Build-Status geändert: {proj_name} ({owner}/{repo_name})"]
    for wf_name, run in by_wf.items():
        c = run_conclusion(run)
        num = run.get("run_number", "?")
        branch = run.get("head_branch", "?")
        lines.append(f"  {_icon(c)} {wf_name} · #{num} · {branch} · {c}")
    if conclusion == "failure":
        lines.append(
            f"\nSoll ich die Fehler analysieren?\n"
            f"→ !ai @{proj_name} Analysiere den fehlgeschlagenen GitHub Build und behebe die Fehler"
        )
    return "\n".join(lines)


def format_ghstatus(status_list: list[dict]) -> str:
    """Format a list of project status dicts (from ``fetch_status_for_projects``)
    into a human-readable Matrix message.
    """
    if not status_list:
        return "📊 Keine Projekte mit GitHub-Remote gefunden."
    lines = ["📊 GitHub Actions Status"]
    for entry in status_list:
        name = entry["name"]
        if "error" in entry:
            lines.append(f"\n⚪ {name}: {entry['error']}")
            continue
        c = entry["conclusion"]
        lines.append(f"\n{_icon(c)} {name} ({entry['owner']}/{entry['repo']})")
        for wf_name, run in entry.get("by_workflow", {}).items():
            rc = run_conclusion(run)
            num = run.get("run_number", "?")
            branch = run.get("head_branch", "?")
            lines.append(f"  {_icon(rc)} {wf_name} · #{num} · {branch} · {rc}")
    return "\n".join(lines)


def _icon(conclusion: str) -> str:
    if conclusion == "success":
        return "✅"
    if conclusion == "in_progress":
        return "⏳"
    if conclusion in ("failure", "timed_out", "startup_failure"):
        return "❌"
    if conclusion == "cancelled":
        return "🚫"
    return "⚪"
