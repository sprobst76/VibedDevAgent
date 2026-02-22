"""Tests for runner/tmux_driver.py — TmuxDriver."""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from runner.tmux_driver import TmuxDriver


class TestSessionExists(unittest.TestCase):
    def _driver(self):
        return TmuxDriver(tmux_bin="tmux")

    def test_returns_true_when_has_session_succeeds(self):
        driver = self._driver()
        fake_result = MagicMock()
        fake_result.returncode = 0
        with patch.object(driver, "_run_tmux", return_value=fake_result) as mock_run:
            result = driver.session_exists(job_id="abc123")
        self.assertTrue(result)
        mock_run.assert_called_once_with(
            "has-session", "-t", "devagent-job-abc123", check=False
        )

    def test_returns_false_when_has_session_fails(self):
        driver = self._driver()
        fake_result = MagicMock()
        fake_result.returncode = 1
        with patch.object(driver, "_run_tmux", return_value=fake_result):
            result = driver.session_exists(job_id="abc123")
        self.assertFalse(result)

    def test_session_name_format(self):
        self.assertEqual(TmuxDriver.session_name("xyz"), "devagent-job-xyz")

    def test_session_exists_uses_correct_session_name(self):
        driver = self._driver()
        fake_result = MagicMock()
        fake_result.returncode = 0
        with patch.object(driver, "_run_tmux", return_value=fake_result) as mock_run:
            driver.session_exists(job_id="my-job-99")
        args = mock_run.call_args[0]
        self.assertIn("devagent-job-my-job-99", args)
