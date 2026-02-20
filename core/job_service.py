"""Coordinates job creation from Matrix JobCards."""

from __future__ import annotations

from dataclasses import dataclass

from adapters.matrix.jobcard import JobCard
from core.audit import append_audit_event
from core.engine import DevAgentEngine
from core.worktree_manager import WorktreeManager


@dataclass(frozen=True)
class PreparedJob:
    job_id: str
    worktree_path: str


class JobService:
    def __init__(self, engine: DevAgentEngine, worktrees: WorktreeManager) -> None:
        self.engine = engine
        self.worktrees = worktrees

    def create_from_jobcard(self, card: JobCard) -> PreparedJob:
        self.engine.create_job(card.job_id)
        worktree_path = self.worktrees.create(card.repo, card.job_id, card.branch)
        self.engine.advance_to_wait_approval(card.job_id)
        append_audit_event(
            artifacts_root=self.engine.artifacts_root,
            job_id=card.job_id,
            action="job_created",
            user_id=card.requested_by,
            state_before="RECEIVED",
            state_after="WAIT_APPROVAL",
            allowed=True,
            reason="job card accepted",
            extra={"repo": card.repo, "branch": card.branch, "worktree": worktree_path},
        )
        return PreparedJob(job_id=card.job_id, worktree_path=worktree_path)

    def cleanup(self, card: JobCard) -> str:
        result = self.worktrees.cleanup(card.repo, card.job_id)
        append_audit_event(
            artifacts_root=self.engine.artifacts_root,
            job_id=card.job_id,
            action="worktree_cleanup",
            user_id=card.requested_by,
            state_before=self.engine.get_job(card.job_id).state.value,
            state_after=self.engine.get_job(card.job_id).state.value,
            allowed=True,
            reason="cleanup triggered",
        )
        return result
