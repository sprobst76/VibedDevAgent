from __future__ import annotations

import unittest

from adapters.matrix.reactions import evaluate_reaction, map_reaction_to_event
from core.models import JobEvent, JobState


class MatrixReactionTests(unittest.TestCase):
    def test_reaction_mapping(self) -> None:
        self.assertEqual(map_reaction_to_event("✅"), JobEvent.APPROVE)
        self.assertEqual(map_reaction_to_event("❌"), JobEvent.REJECT)
        self.assertEqual(map_reaction_to_event("🛑"), JobEvent.STOP)
        self.assertIsNone(map_reaction_to_event("👍"))

    def test_user_must_be_allowed(self) -> None:
        decision = evaluate_reaction(
            reaction="✅",
            state=JobState.WAIT_APPROVAL,
            user_id="@mallory:example.org",
            allowed_users={"@alice:example.org"},
        )
        self.assertFalse(decision.accepted)
        self.assertIn("not allowed", decision.reason)

    def test_state_guard_is_enforced(self) -> None:
        decision = evaluate_reaction(
            reaction="✅",
            state=JobState.RUNNING,
            user_id="@alice:example.org",
            allowed_users={"@alice:example.org"},
        )
        self.assertFalse(decision.accepted)
        self.assertIn("not allowed", decision.reason)


if __name__ == "__main__":
    unittest.main()
