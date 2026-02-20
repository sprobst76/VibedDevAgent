"""Matrix reaction parsing and guard evaluation."""

from __future__ import annotations

from dataclasses import dataclass

from core.models import JobEvent, JobState
from core.security import is_user_allowed
from core.state_machine import TransitionDecision, apply_event

EMOJI_TO_EVENT: dict[str, JobEvent] = {
    "✅": JobEvent.APPROVE,
    "❌": JobEvent.REJECT,
    "🛑": JobEvent.STOP,
}


@dataclass(frozen=True)
class ReactionDecision:
    accepted: bool
    reason: str
    event: JobEvent | None
    transition: TransitionDecision | None


def map_reaction_to_event(reaction: str) -> JobEvent | None:
    """Translate matrix emoji reaction into a job event."""
    return EMOJI_TO_EVENT.get(reaction)


def evaluate_reaction(
    *,
    reaction: str,
    state: JobState,
    user_id: str,
    allowed_users: set[str],
) -> ReactionDecision:
    """Check emoji support, authorization and state guard in one step."""
    event = map_reaction_to_event(reaction)
    if event is None:
        return ReactionDecision(
            accepted=False,
            reason=f"unsupported reaction '{reaction}'",
            event=None,
            transition=None,
        )

    if not is_user_allowed(user_id, allowed_users):
        return ReactionDecision(
            accepted=False,
            reason=f"user '{user_id}' is not allowed",
            event=event,
            transition=None,
        )

    transition = apply_event(state, event)
    if not transition.allowed:
        return ReactionDecision(
            accepted=False,
            reason=transition.reason,
            event=event,
            transition=transition,
        )

    return ReactionDecision(
        accepted=True,
        reason="ok",
        event=event,
        transition=transition,
    )
