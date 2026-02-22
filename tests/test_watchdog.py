"""Tests for core/watchdog.py — JobWatchdog."""
from __future__ import annotations

import time
import unittest
from unittest.mock import MagicMock

from core.engine import DevAgentEngine
from core.models import JobState
from core.watchdog import JobWatchdog


def _make_watchdog(engine, tmux, room_id_for=None, notify_fn=None, max_job_seconds=3600):
    if room_id_for is None:
        room_id_for = lambda job_id: f"!room-for-{job_id}:example.org"  # noqa: E731
    if notify_fn is None:
        notify_fn = MagicMock()
    return JobWatchdog(
        engine=engine,
        tmux=tmux,
        room_id_for=room_id_for,
        notify_fn=notify_fn,
        check_interval=9999,   # never fires automatically in tests
        max_job_seconds=max_job_seconds,
    )


def _engine_with_running_job(job_id: str, started_at: float | None = None) -> DevAgentEngine:
    engine = DevAgentEngine()
    record = engine.create_job(job_id)
    record.state = JobState.RUNNING
    if started_at is not None:
        record.started_at = started_at
    return engine


class TestWatchdogOrphanedSession(unittest.TestCase):
    def test_dead_session_triggers_fail_and_notify(self):
        engine = _engine_with_running_job("j1")
        tmux = MagicMock()
        tmux.session_exists.return_value = False
        notify = MagicMock()
        wd = _make_watchdog(engine, tmux, notify_fn=notify)

        wd._check_once()

        self.assertEqual(engine.jobs["j1"].state, JobState.FAILED)
        notify.assert_called_once()
        args = notify.call_args[0]
        self.assertIn("j1", args[1])

    def test_healthy_session_not_touched(self):
        engine = _engine_with_running_job("j2", started_at=time.time())
        tmux = MagicMock()
        tmux.session_exists.return_value = True
        notify = MagicMock()
        wd = _make_watchdog(engine, tmux, notify_fn=notify, max_job_seconds=9999)

        wd._check_once()

        self.assertEqual(engine.jobs["j2"].state, JobState.RUNNING)
        notify.assert_not_called()

    def test_no_rooms_no_notify_on_dead_session(self):
        """If room_id_for returns None, notify_fn must not be called."""
        engine = _engine_with_running_job("j3")
        tmux = MagicMock()
        tmux.session_exists.return_value = False
        notify = MagicMock()
        wd = _make_watchdog(engine, tmux, room_id_for=lambda _: None, notify_fn=notify)

        wd._check_once()

        self.assertEqual(engine.jobs["j3"].state, JobState.FAILED)
        notify.assert_not_called()


class TestWatchdogHardTimeout(unittest.TestCase):
    def test_expired_job_stopped_and_failed(self):
        old_start = time.time() - 7201
        engine = _engine_with_running_job("j4", started_at=old_start)
        tmux = MagicMock()
        tmux.session_exists.return_value = True
        notify = MagicMock()
        wd = _make_watchdog(engine, tmux, notify_fn=notify, max_job_seconds=7200)

        wd._check_once()

        tmux.stop_session.assert_called_once_with(job_id="j4")
        self.assertEqual(engine.jobs["j4"].state, JobState.FAILED)
        notify.assert_called_once()

    def test_job_without_started_at_not_timed_out(self):
        """started_at == 0.0 means we don't know the start time, so skip timeout."""
        engine = _engine_with_running_job("j5", started_at=0.0)
        tmux = MagicMock()
        tmux.session_exists.return_value = True
        notify = MagicMock()
        wd = _make_watchdog(engine, tmux, notify_fn=notify, max_job_seconds=1)

        wd._check_once()

        self.assertEqual(engine.jobs["j5"].state, JobState.RUNNING)
        tmux.stop_session.assert_not_called()
        notify.assert_not_called()


class TestWatchdogTerminalJobsSkipped(unittest.TestCase):
    def test_failed_job_not_rechecked(self):
        engine = DevAgentEngine()
        record = engine.create_job("j6")
        record.state = JobState.FAILED
        tmux = MagicMock()
        wd = _make_watchdog(engine, tmux)

        wd._check_once()

        tmux.session_exists.assert_not_called()

    def test_done_job_not_rechecked(self):
        engine = DevAgentEngine()
        record = engine.create_job("j7")
        record.state = JobState.DONE
        tmux = MagicMock()
        wd = _make_watchdog(engine, tmux)

        wd._check_once()

        tmux.session_exists.assert_not_called()


class TestWatchdogExceptionIsolation(unittest.TestCase):
    def test_exception_in_session_check_does_not_crash_watchdog(self):
        engine = _engine_with_running_job("j8")
        tmux = MagicMock()
        tmux.session_exists.side_effect = RuntimeError("tmux exploded")
        wd = _make_watchdog(engine, tmux)

        # Should not raise; logs error at WARNING/ERROR level
        with self.assertLogs("core.watchdog", level="ERROR"):
            wd._check_once()

        # Job state unchanged — watchdog failed before it could act
        self.assertEqual(engine.jobs["j8"].state, JobState.RUNNING)
