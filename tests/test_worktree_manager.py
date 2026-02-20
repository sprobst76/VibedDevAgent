from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from core.worktree_manager import WorktreeManager


class WorktreeManagerTests(unittest.TestCase):
    @patch("subprocess.run")
    def test_create_calls_expected_script(self, run_mock: Mock) -> None:
        run_mock.return_value = Mock(stdout="/srv/agent-worktrees/repo/job-1\n")
        manager = WorktreeManager(scripts_dir="scripts")

        out = manager.create("repo", "1", "main")

        self.assertEqual(out, "/srv/agent-worktrees/repo/job-1")
        run_mock.assert_called_once()
        cmd = run_mock.call_args[0][0]
        self.assertTrue(cmd[0].endswith("scripts/worktree_create.sh"))
        self.assertEqual(cmd[1:], ["repo", "1", "main"])

    @patch("subprocess.run")
    def test_diff_calls_expected_script(self, run_mock: Mock) -> None:
        run_mock.return_value = Mock(stdout="/srv/agent-artifacts/job-1/diff.patch\n")
        manager = WorktreeManager(scripts_dir="scripts")

        out = manager.diff("repo", "1", "origin/main")

        self.assertEqual(out, "/srv/agent-artifacts/job-1/diff.patch")
        cmd = run_mock.call_args[0][0]
        self.assertTrue(cmd[0].endswith("scripts/worktree_diff.sh"))

    @patch("subprocess.run")
    def test_cleanup_calls_expected_script(self, run_mock: Mock) -> None:
        run_mock.return_value = Mock(stdout="Removed worktree\n")
        manager = WorktreeManager(scripts_dir="scripts")

        out = manager.cleanup("repo", "1")

        self.assertIn("Removed", out)
        cmd = run_mock.call_args[0][0]
        self.assertTrue(cmd[0].endswith("scripts/worktree_cleanup.sh"))


if __name__ == "__main__":
    unittest.main()
