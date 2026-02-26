"""Tests for adapters/github/client.py and core/ci_monitor.format_ghstatus."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from adapters.github.client import (
    detect_github_repo,
    latest_per_workflow,
    overall_conclusion,
    run_conclusion,
)
from core.ci_monitor import format_ghstatus


# ── detect_github_repo ────────────────────────────────────────────────────────

def _make_git_repo(tmp: str, url: str) -> str:
    """Create a fake .git/config with the given remote origin url."""
    git_dir = Path(tmp) / ".git"
    git_dir.mkdir()
    (git_dir / "config").write_text(
        f'[core]\n\trepositoryformatversion = 0\n'
        f'[remote "origin"]\n\turl = {url}\n\tfetch = +refs/heads/*:refs/remotes/origin/*\n',
        encoding="utf-8",
    )
    return tmp


class DetectGitHubRepoTests(unittest.TestCase):
    def _detect(self, url: str) -> tuple[str, str] | None:
        with tempfile.TemporaryDirectory() as tmp:
            path = _make_git_repo(tmp, url)
            return detect_github_repo(path)

    def test_https_url_with_dot_git(self):
        self.assertEqual(self._detect("https://github.com/owner/my-repo.git"), ("owner", "my-repo"))

    def test_https_url_without_dot_git(self):
        self.assertEqual(self._detect("https://github.com/owner/my-repo"), ("owner", "my-repo"))

    def test_ssh_url(self):
        self.assertEqual(self._detect("git@github.com:owner/my-repo.git"), ("owner", "my-repo"))

    def test_ssh_url_without_dot_git(self):
        self.assertEqual(self._detect("git@github.com:owner/my-repo"), ("owner", "my-repo"))

    def test_non_github_remote_returns_none(self):
        self.assertIsNone(self._detect("https://gitlab.com/owner/repo.git"))

    def test_no_git_dir_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(detect_github_repo(tmp))

    def test_read_error_returns_none(self):
        self.assertIsNone(detect_github_repo("/nonexistent/path/xyz"))


# ── latest_per_workflow ───────────────────────────────────────────────────────

class LatestPerWorkflowTests(unittest.TestCase):
    def test_deduplicates_by_workflow_name(self):
        runs = [
            {"name": "test", "run_number": 3},
            {"name": "lint", "run_number": 3},
            {"name": "test", "run_number": 2},  # older — should be ignored
        ]
        result = latest_per_workflow(runs)
        self.assertEqual(len(result), 2)
        self.assertEqual(result["test"]["run_number"], 3)

    def test_empty_list_returns_empty_dict(self):
        self.assertEqual(latest_per_workflow([]), {})

    def test_single_run(self):
        runs = [{"name": "build", "run_number": 1}]
        result = latest_per_workflow(runs)
        self.assertEqual(list(result.keys()), ["build"])


# ── run_conclusion ────────────────────────────────────────────────────────────

class RunConclusionTests(unittest.TestCase):
    def test_active_status_returns_in_progress(self):
        for status in ("queued", "in_progress", "waiting", "requested", "pending"):
            with self.subTest(status=status):
                run = {"status": status, "conclusion": None}
                self.assertEqual(run_conclusion(run), "in_progress")

    def test_completed_with_success(self):
        run = {"status": "completed", "conclusion": "success"}
        self.assertEqual(run_conclusion(run), "success")

    def test_completed_with_failure(self):
        run = {"status": "completed", "conclusion": "failure"}
        self.assertEqual(run_conclusion(run), "failure")


# ── overall_conclusion ────────────────────────────────────────────────────────

class OverallConclusionTests(unittest.TestCase):
    def _make(self, conclusions: list[str]) -> dict:
        """Build a by_workflow dict from a list of conclusion strings."""
        return {
            f"wf{i}": {"status": "completed", "conclusion": c}
            for i, c in enumerate(conclusions)
        }

    def test_all_success(self):
        self.assertEqual(overall_conclusion(self._make(["success", "success"])), "success")

    def test_any_failure_wins(self):
        self.assertEqual(overall_conclusion(self._make(["success", "failure"])), "failure")

    def test_in_progress_no_failure(self):
        by_wf = {"wf0": {"status": "in_progress", "conclusion": None}}
        self.assertEqual(overall_conclusion(by_wf), "in_progress")

    def test_failure_beats_in_progress(self):
        by_wf = {
            "wf0": {"status": "in_progress", "conclusion": None},
            "wf1": {"status": "completed", "conclusion": "failure"},
        }
        self.assertEqual(overall_conclusion(by_wf), "failure")

    def test_empty_returns_unknown(self):
        self.assertEqual(overall_conclusion({}), "unknown")


# ── format_ghstatus ───────────────────────────────────────────────────────────

class FormatGhstatusTests(unittest.TestCase):
    def _make_entry(self, name, conclusion, wf_conclusions=None):
        by_wf = {}
        if wf_conclusions:
            for i, c in enumerate(wf_conclusions):
                by_wf[f"wf{i}"] = {"status": "completed", "conclusion": c,
                                    "run_number": i + 1, "head_branch": "main"}
        return {"name": name, "owner": "owner", "repo": name.lower(),
                "conclusion": conclusion, "by_workflow": by_wf}

    def test_success_project_shows_checkmark(self):
        entries = [self._make_entry("MyProject", "success", ["success"])]
        text = format_ghstatus(entries)
        self.assertIn("✅", text)
        self.assertIn("MyProject", text)

    def test_failure_project_shows_cross(self):
        entries = [self._make_entry("BrokenProject", "failure", ["failure"])]
        text = format_ghstatus(entries)
        self.assertIn("❌", text)

    def test_no_remote_shows_circle(self):
        entries = [{"name": "NoGit", "error": "kein GitHub-Remote erkannt"}]
        text = format_ghstatus(entries)
        self.assertIn("⚪", text)
        self.assertIn("NoGit", text)

    def test_empty_list_returns_hint(self):
        text = format_ghstatus([])
        self.assertIn("Keine Projekte", text)


if __name__ == "__main__":
    unittest.main()
