"""Telegram-side command handling with shared state/security rules."""

from __future__ import annotations

from dataclasses import dataclass

from core.engine import DevAgentEngine
from core.models import JobState

from adapters.telegram.commands import TelegramCommand


@dataclass(frozen=True)
class TelegramDecision:
    accepted: bool
    message: str


def handle_command(
    *,
    engine: DevAgentEngine,
    command: TelegramCommand,
    user_id: str,
    allowed_users: set[str],
) -> TelegramDecision:
    """Execute telegram control command using the same engine gates."""
    if user_id not in allowed_users:
        return TelegramDecision(False, "user not allowed")

    if command.job_id not in engine.jobs:
        return TelegramDecision(False, "unknown job")

    if command.name == "status":
        state = engine.get_job(command.job_id).state.value
        return TelegramDecision(True, f"job {command.job_id}: {state}")

    reaction = "✅" if command.name == "approve" else "🛑"
    decision = engine.handle_matrix_reaction(
        job_id=command.job_id,
        reaction=reaction,
        user_id=user_id,
        allowed_users=allowed_users,
    )

    if not decision.accepted:
        return TelegramDecision(False, decision.reason)

    state = engine.get_job(command.job_id).state
    if state == JobState.CANCELLED:
        return TelegramDecision(True, f"job {command.job_id}: cancelled")
    return TelegramDecision(True, f"job {command.job_id}: {state.value}")
