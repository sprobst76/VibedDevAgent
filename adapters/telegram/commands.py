"""Telegram command parsing for DevAgent control actions."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TelegramCommand:
    name: str
    job_id: str


def parse_command(text: str) -> TelegramCommand | None:
    """Parse '/approve 123', '/stop 123', '/status 123'."""
    parts = text.strip().split()
    if len(parts) != 2:
        return None

    raw_name, job_id = parts
    if not raw_name.startswith("/"):
        return None

    name = raw_name[1:].strip().lower()
    if name not in {"approve", "stop", "status"}:
        return None

    if not job_id:
        return None

    return TelegramCommand(name=name, job_id=job_id)
