"""In-process Matrix room listener utilities."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from adapters.matrix.jobcard import JobCard


@dataclass(frozen=True)
class MatrixListenerConfig:
    room_id: str
    allowed_senders: set[str]


class MatrixRoomListener:
    """Filters room events and extracts valid DevAgent job requests."""

    def __init__(self, config: MatrixListenerConfig) -> None:
        self.config = config

    def extract_job_request(self, event: dict[str, Any]) -> JobCard | None:
        room_id = event.get("room_id")
        sender = event.get("sender")

        if room_id != self.config.room_id:
            return None

        if self.config.allowed_senders and sender not in self.config.allowed_senders:
            return None

        try:
            return JobCard.from_matrix_event(event)
        except ValueError:
            return None
