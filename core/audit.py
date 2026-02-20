"""Audit logging utilities for approval/reject/stop decisions."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def append_audit_event(
    artifacts_root: str,
    job_id: str,
    action: str,
    user_id: str,
    state_before: str,
    state_after: str,
    allowed: bool,
    reason: str,
    extra: dict[str, Any] | None = None,
) -> Path:
    """Append an audit record to job-local jsonl file and return path."""
    job_dir = Path(artifacts_root) / f"job-{job_id}"
    job_dir.mkdir(parents=True, exist_ok=True)
    audit_file = job_dir / "audit.jsonl"

    payload: dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "job_id": job_id,
        "action": action,
        "user_id": user_id,
        "state_before": state_before,
        "state_after": state_after,
        "allowed": allowed,
        "reason": reason,
    }
    if extra:
        payload["extra"] = extra

    with audit_file.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=True) + "\n")

    return audit_file
