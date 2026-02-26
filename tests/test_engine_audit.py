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


class LoadFromArtifactsTests(unittest.TestCase):
    """Tests for DevAgentEngine.load_from_artifacts()."""

    def _write_audit(self, job_dir: Path, events: list[dict]) -> None:
        job_dir.mkdir(parents=True, exist_ok=True)
        audit = job_dir / "audit.jsonl"
        with audit.open("w", encoding="utf-8") as fh:
            for ev in events:
                fh.write(json.dumps(ev) + "\n")

    def test_restores_wait_approval_at_for_waiting_job(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self._write_audit(
                Path(tmp) / "job-w1",
                [{"timestamp": "2026-02-20T10:00:00Z", "action": "receive", "state_after": "WAIT_APPROVAL",
                  "state_before": "RECEIVED", "allowed": True, "reason": "", "job_id": "w1", "user_id": "u"}],
            )
            engine = DevAgentEngine(artifacts_root=tmp)
            record = engine.create_job("w1")
            record.state = JobState.WAIT_APPROVAL
            # Simulate restart: wait_approval_at is 0.0
            self.assertEqual(record.wait_approval_at, 0.0)

            engine.load_from_artifacts()

            self.assertGreater(record.wait_approval_at, 0.0)

    def test_restores_started_at_for_running_job(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self._write_audit(
                Path(tmp) / "job-r1",
                [
                    {"timestamp": "2026-02-20T09:00:00Z", "action": "approve", "state_after": "RUNNING",
                     "state_before": "WAIT_APPROVAL", "allowed": True, "reason": "", "job_id": "r1", "user_id": "u"},
                    {"timestamp": "2026-02-20T09:00:01Z", "action": "runner_start", "state_after": "RUNNING",
                     "state_before": "RUNNING", "allowed": True, "reason": "runner session started", "job_id": "r1",
                     "user_id": "u"},
                ],
            )
            engine = DevAgentEngine(artifacts_root=tmp)
            record = engine.create_job("r1")
            record.state = JobState.RUNNING
            self.assertEqual(record.started_at, 0.0)

            engine.load_from_artifacts()

            self.assertGreater(record.started_at, 0.0)

    def test_skips_terminal_jobs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self._write_audit(
                Path(tmp) / "job-d1",
                [{"timestamp": "2026-02-20T10:00:00Z", "action": "done", "state_after": "DONE",
                  "state_before": "RUNNING", "allowed": True, "reason": "", "job_id": "d1", "user_id": "u"}],
            )
            engine = DevAgentEngine(artifacts_root=tmp)
            record = engine.create_job("d1")
            record.state = JobState.DONE

            engine.load_from_artifacts()

            # No timestamps should be set (DONE is terminal)
            self.assertEqual(record.started_at, 0.0)
            self.assertEqual(record.wait_approval_at, 0.0)

    def test_skips_missing_audit_file_gracefully(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = DevAgentEngine(artifacts_root=tmp)
            record = engine.create_job("missing")
            record.state = JobState.WAIT_APPROVAL

            # Should not raise even if no audit.jsonl exists
            engine.load_from_artifacts()
            self.assertEqual(record.wait_approval_at, 0.0)

    def test_does_not_overwrite_existing_timestamp(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self._write_audit(
                Path(tmp) / "job-x1",
                [{"timestamp": "2026-02-20T10:00:00Z", "action": "receive", "state_after": "WAIT_APPROVAL",
                  "state_before": "RECEIVED", "allowed": True, "reason": "", "job_id": "x1", "user_id": "u"}],
            )
            engine = DevAgentEngine(artifacts_root=tmp)
            record = engine.create_job("x1")
            record.state = JobState.WAIT_APPROVAL
            existing_ts = 1_700_000_000.0
            record.wait_approval_at = existing_ts  # already set

            engine.load_from_artifacts()

            # Should not be overwritten (was already set)
            self.assertEqual(record.wait_approval_at, existing_ts)

    def test_skips_jobs_not_in_engine(self) -> None:
        """load_from_artifacts only processes jobs already in self.jobs."""
        with tempfile.TemporaryDirectory() as tmp:
            self._write_audit(
                Path(tmp) / "job-orphan",
                [{"timestamp": "2026-02-20T10:00:00Z", "action": "receive", "state_after": "WAIT_APPROVAL",
                  "state_before": "RECEIVED", "allowed": True, "reason": "", "job_id": "orphan", "user_id": "u"}],
            )
            engine = DevAgentEngine(artifacts_root=tmp)
            # Do NOT create job "orphan" in engine

            engine.load_from_artifacts()

            self.assertNotIn("orphan", engine.jobs)


if __name__ == "__main__":
    unittest.main()
