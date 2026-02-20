from __future__ import annotations

import unittest

from core.models import JobEvent, JobState
from core.state_machine import apply_event


class StateMachineTests(unittest.TestCase):
    def test_approve_allowed_only_in_wait_approval(self) -> None:
        allowed = apply_event(JobState.WAIT_APPROVAL, JobEvent.APPROVE)
        denied = apply_event(JobState.RUNNING, JobEvent.APPROVE)

        self.assertTrue(allowed.allowed)
        self.assertEqual(allowed.state_after, JobState.RUNNING)
        self.assertFalse(denied.allowed)
        self.assertEqual(denied.state_after, JobState.RUNNING)

    def test_stop_from_running_moves_to_cancelled(self) -> None:
        decision = apply_event(JobState.RUNNING, JobEvent.STOP)
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.state_after, JobState.CANCELLED)


if __name__ == "__main__":
    unittest.main()
