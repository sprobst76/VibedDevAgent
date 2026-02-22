"""Tests for terminal-state guard in DevAgentEngine.handle_matrix_reaction."""
from __future__ import annotations

import tempfile
import unittest

from core.engine import DevAgentEngine
from core.models import JobState


_ALLOWED = {"@alice:example.org"}


def _engine_with_job(job_id: str, state: JobState, artifacts_root: str = "/tmp") -> DevAgentEngine:
    engine = DevAgentEngine(artifacts_root=artifacts_root)
    record = engine.create_job(job_id)
    record.state = state
    return engine


class TestTerminalStateGuard(unittest.TestCase):
    """Reactions on DONE/FAILED/CANCELLED jobs must be silently rejected."""

    def _react(self, engine, job_id, reaction="✅"):
        return engine.handle_matrix_reaction(
            job_id=job_id,
            reaction=reaction,
            user_id="@alice:example.org",
            allowed_users=_ALLOWED,
        )

    def test_approve_on_done_job_rejected(self):
        engine = _engine_with_job("j1", JobState.DONE)
        decision = self._react(engine, "j1", "✅")
        self.assertFalse(decision.accepted)
        self.assertIn("terminal", decision.reason)
        # State must not change
        self.assertEqual(engine.jobs["j1"].state, JobState.DONE)

    def test_approve_on_failed_job_rejected(self):
        engine = _engine_with_job("j2", JobState.FAILED)
        decision = self._react(engine, "j2", "✅")
        self.assertFalse(decision.accepted)
        self.assertEqual(engine.jobs["j2"].state, JobState.FAILED)

    def test_stop_on_cancelled_job_rejected(self):
        engine = _engine_with_job("j3", JobState.CANCELLED)
        decision = self._react(engine, "j3", "🛑")
        self.assertFalse(decision.accepted)
        self.assertEqual(engine.jobs["j3"].state, JobState.CANCELLED)

    def test_reaction_on_wait_approval_still_works(self):
        """Non-terminal state must still accept valid reactions."""
        with tempfile.TemporaryDirectory() as tmp:
            engine = _engine_with_job("j4", JobState.WAIT_APPROVAL, artifacts_root=tmp)
            decision = self._react(engine, "j4", "✅")
        self.assertTrue(decision.accepted)
        self.assertEqual(engine.jobs["j4"].state, JobState.RUNNING)

    def test_multiple_reactions_on_done_all_rejected(self):
        engine = _engine_with_job("j5", JobState.DONE)
        for _ in range(3):
            decision = self._react(engine, "j5", "✅")
            self.assertFalse(decision.accepted)
        self.assertEqual(engine.jobs["j5"].state, JobState.DONE)
