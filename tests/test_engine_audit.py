from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from core.engine import DevAgentEngine
from core.models import JobState
from runner.job_runner import JobRunHandle


class EngineAuditTests(unittest.TestCase):
    def test_reaction_updates_state_and_writes_audit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = DevAgentEngine(artifacts_root=tmp)
            engine.create_job("00001")
            engine.advance_to_wait_approval("00001")

            decision = engine.handle_matrix_reaction(
                job_id="00001",
                reaction="✅",
                user_id="@alice:example.org",
                allowed_users={"@alice:example.org"},
            )
            self.assertTrue(decision.accepted)
            self.assertEqual(engine.get_job("00001").state, JobState.RUNNING)

            audit_file = Path(tmp) / "job-00001" / "audit.jsonl"
            self.assertTrue(audit_file.exists())
            payload = json.loads(audit_file.read_text(encoding="utf-8").strip())
            self.assertEqual(payload["action"], "approve")
            self.assertTrue(payload["allowed"])

    def test_approve_can_start_runner(self) -> None:
        class FakeRunner:
            def __init__(self) -> None:
                self.started = False
                self.stopped = False

            def start(self, spec):  # type: ignore[no-untyped-def]
                self.started = True
                return JobRunHandle(job_id=spec.job_id, session_name="devagent-job-00002", log_file="/tmp/log")

            def stop(self, *, job_id: str) -> bool:
                self.stopped = True
                return True

        with tempfile.TemporaryDirectory() as tmp:
            fake_runner = FakeRunner()
            engine = DevAgentEngine(artifacts_root=tmp, runner=fake_runner)  # type: ignore[arg-type]
            engine.create_job("00002")
            engine.advance_to_wait_approval("00002")

            decision = engine.handle_matrix_reaction(
                job_id="00002",
                reaction="✅",
                user_id="@alice:example.org",
                allowed_users={"@alice:example.org"},
                run_command="echo hello",
                run_cwd="/tmp",
            )
            self.assertTrue(decision.accepted)
            self.assertTrue(fake_runner.started)

            stop_decision = engine.handle_matrix_reaction(
                job_id="00002",
                reaction="🛑",
                user_id="@alice:example.org",
                allowed_users={"@alice:example.org"},
            )
            self.assertTrue(stop_decision.accepted)
            self.assertTrue(fake_runner.stopped)
            self.assertEqual(engine.get_job("00002").state, JobState.CANCELLED)


if __name__ == "__main__":
    unittest.main()
