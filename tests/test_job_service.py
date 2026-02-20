from __future__ import annotations

import tempfile
import unittest

from adapters.matrix.jobcard import JobCard
from core.engine import DevAgentEngine
from core.job_service import JobService


class FakeWorktreeManager:
    def create(self, repo: str, job_id: str, base_branch: str = "main") -> str:
        return f"/tmp/worktrees/{repo}/job-{job_id}"

    def cleanup(self, repo: str, job_id: str) -> str:
        return f"Removed worktree /tmp/worktrees/{repo}/job-{job_id}"


class JobServiceTests(unittest.TestCase):
    def test_create_from_jobcard_advances_to_wait_approval(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = DevAgentEngine(artifacts_root=tmp)
            service = JobService(engine, FakeWorktreeManager())
            card = JobCard(
                job_id="123",
                repo="repo",
                branch="main",
                command="make test",
                requested_by="@alice:example.org",
                created_at="2026-02-20T00:00:00Z",
                room_id="!room:example.org",
            )

            prepared = service.create_from_jobcard(card)

            self.assertEqual(prepared.worktree_path, "/tmp/worktrees/repo/job-123")
            self.assertEqual(engine.get_job("123").state.value, "WAIT_APPROVAL")


if __name__ == "__main__":
    unittest.main()
