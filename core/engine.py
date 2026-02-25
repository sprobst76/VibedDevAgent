"""Minimal in-memory orchestration engine for MVP scaffolding."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from adapters.matrix.reactions import ReactionDecision, evaluate_reaction
from core.audit import append_audit_event
from core.idempotency import IdempotencyStore
from core.models import JobEvent, JobState, TERMINAL_STATES
from runner.job_runner import JobRunSpec, JobRunner

log = logging.getLogger(__name__)


@dataclass
class JobRecord:
    job_id: str
    state: JobState
    started_at: float = field(default=0.0)       # epoch seconds; set when RUNNING
    wait_approval_at: float = field(default=0.0)  # epoch seconds; set when WAIT_APPROVAL


class DevAgentEngine:
    """Tracks state and applies reaction-based transitions."""

    def __init__(self, artifacts_root: str = "/srv/agent-artifacts", runner: JobRunner | None = None) -> None:
        self.artifacts_root = artifacts_root
        self.runner = runner
        self.jobs: dict[str, JobRecord] = {}
        self.idempotency = IdempotencyStore()

    def create_job(self, job_id: str) -> JobRecord:
        record = JobRecord(job_id=job_id, state=JobState.RECEIVED)
        self.jobs[job_id] = record
        return record

    def running_jobs(self) -> list[JobRecord]:
        """Return all jobs currently in RUNNING state."""
        return [j for j in self.jobs.values() if j.state == JobState.RUNNING]

    def waiting_jobs(self) -> list[JobRecord]:
        """Return all jobs currently in WAIT_APPROVAL state."""
        return [j for j in self.jobs.values() if j.state == JobState.WAIT_APPROVAL]

    def fail_job(self, job_id: str) -> None:
        """Force-transition a job to FAILED (watchdog path, bypasses state machine)."""
        job = self.jobs.get(job_id)
        if job and job.state not in TERMINAL_STATES:
            job.state = JobState.FAILED
            log.warning("job %s force-failed by watchdog", job_id)

    def advance_to_wait_approval(self, job_id: str) -> JobRecord:
        record = self.jobs[job_id]
        # MVP bootstrap: planning is represented as a quick internal step.
        record.state = JobState.PLANNING
        record.state = JobState.WAIT_APPROVAL
        record.wait_approval_at = time.time()
        return record

    def get_job(self, job_id: str) -> JobRecord:
        return self.jobs[job_id]

    def handle_matrix_reaction(
        self,
        *,
        job_id: str,
        reaction: str,
        user_id: str,
        allowed_users: set[str],
        action_id: str | None = None,
        run_command: str | None = None,
        run_cwd: str | None = None,
    ) -> ReactionDecision:
        record = self.jobs[job_id]
        state_before = record.state

        # Ignore reactions on jobs that have already reached a terminal state.
        if state_before in TERMINAL_STATES:
            log.debug("reaction on terminal job %s (state=%s) ignored", job_id, state_before)
            return ReactionDecision(
                accepted=False,
                reason=f"job already in terminal state {state_before.value}",
                event=None,
                transition=None,
            )

        if action_id:
            key = f"{job_id}:{action_id}"
            if not self.idempotency.mark_once(key):
                append_audit_event(
                    artifacts_root=self.artifacts_root,
                    job_id=job_id,
                    action=f"reaction:{reaction}",
                    user_id=user_id,
                    state_before=state_before.value,
                    state_after=state_before.value,
                    allowed=False,
                    reason=f"duplicate action_id '{action_id}'",
                )
                return ReactionDecision(
                    accepted=False,
                    reason=f"duplicate action_id '{action_id}'",
                    event=None,
                    transition=None,
                )

        decision = evaluate_reaction(
            reaction=reaction,
            state=state_before,
            user_id=user_id,
            allowed_users=allowed_users,
        )

        state_after = state_before
        if decision.accepted and decision.transition is not None:
            state_after = decision.transition.state_after
            record.state = state_after
            if (
                decision.event == JobEvent.APPROVE
                and self.runner is not None
                and run_command
                and run_cwd
            ):
                try:
                    handle = self.runner.start(
                        JobRunSpec(
                            job_id=job_id,
                            command=run_command,
                            cwd=run_cwd,
                            artifacts_root=self.artifacts_root,
                        )
                    )
                    record.started_at = time.time()
                    append_audit_event(
                        artifacts_root=self.artifacts_root,
                        job_id=job_id,
                        action="runner_start",
                        user_id=user_id,
                        state_before=state_after.value,
                        state_after=state_after.value,
                        allowed=True,
                        reason="runner session started",
                        extra={"session_name": handle.session_name, "log_file": handle.log_file},
                    )
                except Exception as exc:
                    record.state = JobState.FAILED
                    state_after = JobState.FAILED
                    append_audit_event(
                        artifacts_root=self.artifacts_root,
                        job_id=job_id,
                        action="runner_start",
                        user_id=user_id,
                        state_before=JobState.RUNNING.value,
                        state_after=JobState.FAILED.value,
                        allowed=False,
                        reason=f"runner start failed: {exc}",
                    )
            if decision.event == JobEvent.STOP and self.runner is not None:
                stopped = self.runner.stop(job_id=job_id)
                append_audit_event(
                    artifacts_root=self.artifacts_root,
                    job_id=job_id,
                    action="runner_stop",
                    user_id=user_id,
                    state_before=state_after.value,
                    state_after=state_after.value,
                    allowed=stopped,
                    reason="runner session stop requested",
                )

        append_audit_event(
            artifacts_root=self.artifacts_root,
            job_id=job_id,
            action=decision.event.value if decision.event else f"reaction:{reaction}",
            user_id=user_id,
            state_before=state_before.value,
            state_after=state_after.value,
            allowed=decision.accepted,
            reason=decision.reason,
            extra={"reaction": reaction},
        )
        return decision
