from __future__ import annotations

import tempfile
import unittest

from core.engine import DevAgentEngine
from core.reliability import run_with_retry


class ReliabilityIdempotencyTests(unittest.TestCase):
    def test_retry_succeeds_after_transient_failure(self) -> None:
        attempts = {"n": 0}

        def op() -> str:
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise TimeoutError("first call failed")
            return "ok"

        self.assertEqual(run_with_retry(op, retries=1), "ok")
        self.assertEqual(attempts["n"], 2)

    def test_duplicate_action_id_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = DevAgentEngine(artifacts_root=tmp)
            engine.create_job("555")
            engine.advance_to_wait_approval("555")

            first = engine.handle_matrix_reaction(
                job_id="555",
                reaction="✅",
                user_id="@alice:example.org",
                allowed_users={"@alice:example.org"},
                action_id="evt-1",
            )
            second = engine.handle_matrix_reaction(
                job_id="555",
                reaction="✅",
                user_id="@alice:example.org",
                allowed_users={"@alice:example.org"},
                action_id="evt-1",
            )

            self.assertTrue(first.accepted)
            self.assertFalse(second.accepted)
            self.assertIn("duplicate action_id", second.reason)


if __name__ == "__main__":
    unittest.main()
