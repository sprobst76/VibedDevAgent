"""Tests for adapters/matrix/ai_handler.py"""
from __future__ import annotations

import threading
import unittest
from unittest.mock import patch

from adapters.matrix.ai_handler import MAX_OUTPUT_CHARS, parse_ai_message, run_ai_task


# ── Helpers ───────────────────────────────────────────────────────────────────

class _MockProc:
    """Minimal subprocess.Popen replacement."""

    def __init__(
        self,
        *,
        returncode: int = 0,
        stdout: str = "",
        stderr: str = "",
        poll_sequence: list | None = None,
    ) -> None:
        self.returncode = returncode
        self._stdout = stdout
        self._stderr = stderr
        # poll_sequence drives what poll() returns per call;
        # once exhausted it always returns returncode.
        self._seq = poll_sequence if poll_sequence is not None else [returncode]
        self._idx = 0
        self.signals_sent: list[int] = []

    def poll(self) -> int | None:
        if self._idx < len(self._seq):
            v = self._seq[self._idx]
            self._idx += 1
            return v
        return self.returncode

    def communicate(self) -> tuple[str, str]:
        return self._stdout, self._stderr

    def send_signal(self, sig: int) -> None:
        self.signals_sent.append(sig)

    def wait(self, timeout: float | None = None) -> int:
        return self.returncode

    def kill(self) -> None:
        pass


# ── parse_ai_message ──────────────────────────────────────────────────────────

class ParseAiMessageTests(unittest.TestCase):
    def test_basic_task(self) -> None:
        result = parse_ai_message("!ai fix the login bug")
        self.assertEqual(result, (None, "fix the login bug"))

    def test_with_repo_prefix(self) -> None:
        result = parse_ai_message("!ai @myrepo add tests for auth")
        self.assertEqual(result, ("myrepo", "add tests for auth"))

    def test_case_insensitive_prefix(self) -> None:
        result = parse_ai_message("!AI Fix the bug")
        self.assertEqual(result, (None, "Fix the bug"))

    def test_mixed_case_with_repo(self) -> None:
        result = parse_ai_message("!Ai @MyRepo some task")
        self.assertEqual(result, ("MyRepo", "some task"))

    def test_not_ai_message_returns_none(self) -> None:
        self.assertIsNone(parse_ai_message("hello world"))

    def test_wrong_prefix_returns_none(self) -> None:
        self.assertIsNone(parse_ai_message("!help"))
        self.assertIsNone(parse_ai_message("!status"))

    def test_empty_task_returns_none(self) -> None:
        self.assertIsNone(parse_ai_message("!ai "))
        self.assertIsNone(parse_ai_message("!ai"))

    def test_repo_with_no_task_returns_none(self) -> None:
        self.assertIsNone(parse_ai_message("!ai @myrepo"))
        self.assertIsNone(parse_ai_message("!ai @myrepo   "))

    def test_leading_whitespace_stripped(self) -> None:
        result = parse_ai_message("  !ai   do something  ")
        self.assertEqual(result, (None, "do something"))

    def test_task_with_special_chars(self) -> None:
        result = parse_ai_message("!ai refactor `foo()` and update tests")
        self.assertEqual(result, (None, "refactor `foo()` and update tests"))


# ── run_ai_task ───────────────────────────────────────────────────────────────

class RunAiTaskTests(unittest.TestCase):
    @patch("time.sleep")
    @patch("subprocess.Popen")
    def test_success_returns_output(self, popen_mock, _sleep) -> None:
        proc = _MockProc(returncode=0, stdout="Done!", poll_sequence=[0])
        popen_mock.return_value = proc

        result = run_ai_task(message="!ai test", cwd="/tmp", claude_bin="claude", timeout_seconds=60)

        self.assertTrue(result.success)
        self.assertEqual(result.output, "Done!")
        self.assertFalse(result.truncated)
        self.assertEqual(result.exit_code, 0)

    @patch("time.sleep")
    @patch("subprocess.Popen")
    def test_failure_returns_exit_code(self, popen_mock, _sleep) -> None:
        proc = _MockProc(returncode=1, stdout="error output", poll_sequence=[1])
        popen_mock.return_value = proc

        result = run_ai_task(message="bad", cwd="/tmp", claude_bin="claude", timeout_seconds=60)

        self.assertFalse(result.success)
        self.assertEqual(result.exit_code, 1)

    @patch("time.sleep")
    @patch("subprocess.Popen")
    def test_fallback_to_stderr_when_stdout_empty(self, popen_mock, _sleep) -> None:
        proc = _MockProc(returncode=0, stdout="", stderr="fallback error", poll_sequence=[0])
        popen_mock.return_value = proc

        result = run_ai_task(message="x", cwd="/tmp", claude_bin="claude", timeout_seconds=60)

        self.assertEqual(result.output, "fallback error")

    @patch("time.sleep")
    @patch("subprocess.Popen")
    def test_output_truncated_at_max_chars(self, popen_mock, _sleep) -> None:
        long_output = "x" * (MAX_OUTPUT_CHARS + 100)
        proc = _MockProc(returncode=0, stdout=long_output, poll_sequence=[0])
        popen_mock.return_value = proc

        result = run_ai_task(message="x", cwd="/tmp", claude_bin="claude", timeout_seconds=60)

        self.assertTrue(result.truncated)
        self.assertEqual(len(result.output), MAX_OUTPUT_CHARS)

    def test_claude_not_found_returns_error_result(self) -> None:
        result = run_ai_task(
            message="task", cwd="/tmp",
            claude_bin="__no_such_bin_xyz__",
            timeout_seconds=60,
        )

        self.assertFalse(result.success)
        self.assertIn("nicht gefunden", result.output.lower())
        self.assertEqual(result.exit_code, -1)

    @patch("time.sleep")
    @patch("subprocess.Popen")
    def test_cancel_event_stops_task(self, popen_mock, _sleep) -> None:
        # poll() keeps returning None → process never finishes
        proc = _MockProc(returncode=0, stdout="", poll_sequence=[None] * 100)
        popen_mock.return_value = proc

        cancel = threading.Event()
        cancel.set()  # already cancelled before we start

        result = run_ai_task(
            message="x", cwd="/tmp", claude_bin="claude",
            timeout_seconds=3600, cancel_event=cancel,
        )

        self.assertFalse(result.success)
        self.assertEqual(result.exit_code, -2)
        self.assertIn("abgebrochen", result.output.lower())
        self.assertTrue(len(proc.signals_sent) > 0)  # SIGTERM was sent

    @patch("time.sleep")
    @patch("subprocess.Popen")
    def test_timeout_kills_process(self, popen_mock, _sleep) -> None:
        proc = _MockProc(returncode=0, poll_sequence=[None] * 100)
        popen_mock.return_value = proc

        result = run_ai_task(
            message="x", cwd="/tmp", claude_bin="claude",
            timeout_seconds=0,  # fires on very first iteration
        )

        self.assertFalse(result.success)
        self.assertEqual(result.exit_code, -1)
        self.assertIn("timeout", result.output.lower())

    @patch("time.sleep")
    @patch("subprocess.Popen")
    def test_skip_permissions_flag_included(self, popen_mock, _sleep) -> None:
        proc = _MockProc(returncode=0, stdout="ok", poll_sequence=[0])
        popen_mock.return_value = proc

        run_ai_task(message="task", cwd="/tmp", claude_bin="claude",
                    timeout_seconds=60, skip_permissions=True)

        cmd = popen_mock.call_args[0][0]
        self.assertIn("--dangerously-skip-permissions", cmd)

    @patch("time.sleep")
    @patch("subprocess.Popen")
    def test_skip_permissions_false_excludes_flag(self, popen_mock, _sleep) -> None:
        proc = _MockProc(returncode=0, stdout="ok", poll_sequence=[0])
        popen_mock.return_value = proc

        run_ai_task(message="task", cwd="/tmp", claude_bin="claude",
                    timeout_seconds=60, skip_permissions=False)

        cmd = popen_mock.call_args[0][0]
        self.assertNotIn("--dangerously-skip-permissions", cmd)

    @patch("time.sleep")
    @patch("subprocess.Popen")
    def test_poll_waits_for_completion(self, popen_mock, _sleep) -> None:
        # First two polls return None, third returns 0
        proc = _MockProc(returncode=0, stdout="result", poll_sequence=[None, None, 0])
        popen_mock.return_value = proc

        result = run_ai_task(message="x", cwd="/tmp", claude_bin="claude", timeout_seconds=60)

        self.assertTrue(result.success)
        self.assertEqual(result.output, "result")


if __name__ == "__main__":
    unittest.main()
