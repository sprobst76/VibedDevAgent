from __future__ import annotations

import tempfile
import unittest

from adapters.matrix.client import MatrixSyncResult
from core.engine import DevAgentEngine
from core.job_service import JobService
from core.matrix_worker import MatrixWorker, MatrixWorkerConfig
from runner.job_runner import JobRunHandle


class FakeMatrixClient:
    def __init__(self) -> None:
        self.notices: list[tuple[str, str]] = []
        self.sync_results: list[MatrixSyncResult] = []

    def sync(self, *, since: str | None, timeout_ms: int = 30000) -> MatrixSyncResult:
        if self.sync_results:
            return self.sync_results.pop(0)
        return MatrixSyncResult(next_batch=since or "tok0", payload={"rooms": {"join": {}}})

    def send_notice(self, *, room_id: str, body: str) -> dict[str, str]:
        self.notices.append((room_id, body))
        return {"event_id": "$notice"}


class FakeRunner:
    def __init__(self) -> None:
        self.started = False
        self.stopped = False

    def start(self, spec):  # type: ignore[no-untyped-def]
        self.started = True
        return JobRunHandle(job_id=spec.job_id, session_name="devagent-job-123", log_file="/tmp/log")

    def stop(self, *, job_id: str) -> bool:
        self.stopped = True
        return True


class FakeWorktrees:
    def __init__(self) -> None:
        self.cleaned: list[tuple[str, str]] = []

    def create(self, repo: str, job_id: str, base_branch: str = "main") -> str:
        return f"/tmp/worktrees/{repo}/job-{job_id}"

    def cleanup(self, repo: str, job_id: str) -> str:
        self.cleaned.append((repo, job_id))
        return "ok"


class MatrixWorkerTests(unittest.TestCase):
    def test_jobcard_then_approve_then_stop(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg = MatrixWorkerConfig(
                homeserver_url="https://matrix.org",
                access_token="token",
                room_id="!room:matrix.org",
                allowed_users={"@alice:example.org"},
                state_file=f"{tmp}/state.json",
                artifacts_root=tmp,
                send_notices=False,
            )
            client = FakeMatrixClient()
            fake_runner = FakeRunner()
            engine = DevAgentEngine(artifacts_root=tmp, runner=fake_runner)  # type: ignore[arg-type]
            worktrees = FakeWorktrees()
            jobs = JobService(engine=engine, worktrees=worktrees)  # type: ignore[arg-type]
            worker = MatrixWorker(config=cfg, client=client, engine=engine, jobs=jobs, worktrees=worktrees)  # type: ignore[arg-type]

            job_event = {
                "event_id": "$job1",
                "sender": "@alice:example.org",
                "type": "m.room.message",
                "content": {
                    "msgtype": "m.notice",
                    "body": (
                        "DEVAGENT_JOBCARD "
                        '{"job_id":"123","repo":"demo","branch":"main","command":"echo test",'
                        '"requested_by":"@alice:example.org","created_at":"2026-02-20T00:00:00Z"}'
                    ),
                },
            }
            worker.process_event(job_event)
            self.assertEqual(engine.get_job("123").state.value, "WAIT_APPROVAL")

            approve_event = {
                "event_id": "$react1",
                "sender": "@alice:example.org",
                "type": "m.reaction",
                "content": {"m.relates_to": {"event_id": "$job1", "key": "✅", "rel_type": "m.annotation"}},
            }
            worker.process_event(approve_event)
            self.assertTrue(fake_runner.started)
            self.assertEqual(engine.get_job("123").state.value, "RUNNING")

            stop_event = {
                "event_id": "$react2",
                "sender": "@alice:example.org",
                "type": "m.reaction",
                "content": {"m.relates_to": {"event_id": "$job1", "key": "🛑", "rel_type": "m.annotation"}},
            }
            worker.process_event(stop_event)
            self.assertTrue(fake_runner.stopped)
            self.assertEqual(engine.get_job("123").state.value, "CANCELLED")
            self.assertEqual(worktrees.cleaned, [("demo", "123")])

    def test_state_restored_across_worker_restart(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg = MatrixWorkerConfig(
                homeserver_url="https://matrix.org",
                access_token="token",
                room_id="!room:matrix.org",
                allowed_users={"@alice:example.org"},
                state_file=f"{tmp}/state.json",
                artifacts_root=tmp,
                send_notices=False,
            )
            client = FakeMatrixClient()

            runner1 = FakeRunner()
            engine1 = DevAgentEngine(artifacts_root=tmp, runner=runner1)  # type: ignore[arg-type]
            worktrees1 = FakeWorktrees()
            jobs1 = JobService(engine=engine1, worktrees=worktrees1)  # type: ignore[arg-type]
            worker1 = MatrixWorker(config=cfg, client=client, engine=engine1, jobs=jobs1, worktrees=worktrees1)  # type: ignore[arg-type]

            job_event = {
                "event_id": "$job1",
                "sender": "@alice:example.org",
                "type": "m.room.message",
                "content": {
                    "msgtype": "m.notice",
                    "body": (
                        "DEVAGENT_JOBCARD "
                        '{"job_id":"555","repo":"demo","branch":"main","command":"echo test",'
                        '"requested_by":"@alice:example.org","created_at":"2026-02-20T00:00:00Z"}'
                    ),
                },
            }
            approve_event = {
                "event_id": "$react1",
                "sender": "@alice:example.org",
                "type": "m.reaction",
                "content": {"m.relates_to": {"event_id": "$job1", "key": "✅", "rel_type": "m.annotation"}},
            }
            worker1.process_event(job_event)
            worker1.process_event(approve_event)
            worker1.state.save(cfg.state_file)

            runner2 = FakeRunner()
            engine2 = DevAgentEngine(artifacts_root=tmp, runner=runner2)  # type: ignore[arg-type]
            worktrees2 = FakeWorktrees()
            jobs2 = JobService(engine=engine2, worktrees=worktrees2)  # type: ignore[arg-type]
            worker2 = MatrixWorker(config=cfg, client=client, engine=engine2, jobs=jobs2, worktrees=worktrees2)  # type: ignore[arg-type]

            self.assertEqual(engine2.get_job("555").state.value, "RUNNING")

            stop_event = {
                "event_id": "$react2",
                "sender": "@alice:example.org",
                "type": "m.reaction",
                "content": {"m.relates_to": {"event_id": "$job1", "key": "🛑", "rel_type": "m.annotation"}},
            }
            worker2.process_event(stop_event)
            self.assertTrue(runner2.stopped)
            self.assertEqual(engine2.get_job("555").state.value, "CANCELLED")


if __name__ == "__main__":
    unittest.main()
