"""JobCard event model for Matrix job requests."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


@dataclass(frozen=True)
class JobCard:
    job_id: str
    repo: str
    branch: str
    command: str
    requested_by: str
    created_at: str
    room_id: str

    @classmethod
    def from_matrix_event(cls, event: dict[str, Any]) -> "JobCard":
        if event.get("type") != "devagent.jobcard":
            raise ValueError("unsupported matrix event type")

        room_id = event.get("room_id")
        if not isinstance(room_id, str) or not room_id:
            raise ValueError("missing room_id")

        content = event.get("content")
        if not isinstance(content, dict):
            raise ValueError("missing content")

        required = ["job_id", "repo", "branch", "command", "requested_by", "created_at"]
        missing = [name for name in required if not content.get(name)]
        if missing:
            raise ValueError(f"missing content fields: {', '.join(missing)}")

        return cls(
            job_id=str(content["job_id"]),
            repo=str(content["repo"]),
            branch=str(content["branch"]),
            command=str(content["command"]),
            requested_by=str(content["requested_by"]),
            created_at=str(content["created_at"]),
            room_id=room_id,
        )


def build_jobcard_event(
    *,
    room_id: str,
    job_id: str,
    repo: str,
    branch: str,
    command: str,
    requested_by: str,
) -> dict[str, Any]:
    """Build a Matrix-compatible job card event payload."""
    created_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    return {
        "type": "devagent.jobcard",
        "room_id": room_id,
        "content": {
            "job_id": job_id,
            "repo": repo,
            "branch": branch,
            "command": command,
            "requested_by": requested_by,
            "created_at": created_at,
        },
    }
