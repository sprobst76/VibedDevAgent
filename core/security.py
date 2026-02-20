"""Authorization helpers for control events."""

from __future__ import annotations


def parse_allowed_users(raw: str | None) -> set[str]:
    """Parse comma-separated user IDs into a canonical set."""
    if not raw:
        return set()

    return {value.strip() for value in raw.split(",") if value.strip()}


def is_user_allowed(user_id: str, allowed_users: set[str]) -> bool:
    """Return True when a control event is allowed for this user."""
    return user_id in allowed_users
