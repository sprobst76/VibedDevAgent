"""MVP transition rules for user-triggered events."""

from __future__ import annotations

from dataclasses import dataclass

from core.models import JobEvent, JobState


TRANSITIONS: dict[tuple[JobState, JobEvent], JobState] = {
    (JobState.WAIT_APPROVAL, JobEvent.APPROVE): JobState.RUNNING,
    (JobState.PLANNING, JobEvent.REJECT): JobState.CANCELLED,
    (JobState.WAIT_APPROVAL, JobEvent.REJECT): JobState.CANCELLED,
    (JobState.RUNNING, JobEvent.STOP): JobState.CANCELLED,
    (JobState.RUN_TESTS, JobEvent.STOP): JobState.CANCELLED,
    (JobState.REVIEWING, JobEvent.STOP): JobState.CANCELLED,
}


@dataclass(frozen=True)
class TransitionDecision:
    allowed: bool
    state_before: JobState
    state_after: JobState
    event: JobEvent
    reason: str


def apply_event(state: JobState, event: JobEvent) -> TransitionDecision:
    """Validate and apply a user event based on MVP guard rules."""
    key = (state, event)
    if key not in TRANSITIONS:
        return TransitionDecision(
            allowed=False,
            state_before=state,
            state_after=state,
            event=event,
            reason=f"event '{event.value}' not allowed in state '{state.value}'",
        )

    new_state = TRANSITIONS[key]
    return TransitionDecision(
        allowed=True,
        state_before=state,
        state_after=new_state,
        event=event,
        reason="ok",
    )
