"""Generate read-only dashboard data from audit logs."""

from __future__ import annotations

import json
from pathlib import Path


def load_latest_job_state(job_dir: Path) -> dict[str, str]:
    audit = job_dir / "audit.jsonl"
    if not audit.exists():
        return {"job_id": job_dir.name.removeprefix("job-"), "state": "UNKNOWN"}

    lines = [line for line in audit.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not lines:
        return {"job_id": job_dir.name.removeprefix("job-"), "state": "UNKNOWN"}

    payload = json.loads(lines[-1])
    return {
        "job_id": str(payload.get("job_id", job_dir.name.removeprefix("job-"))),
        "state": str(payload.get("state_after", "UNKNOWN")),
        "last_action": str(payload.get("action", "unknown")),
        "timestamp": str(payload.get("timestamp", "")),
    }


def generate_dashboard_data(artifacts_root: str, output_file: str) -> None:
    root = Path(artifacts_root)
    jobs: list[dict[str, str]] = []

    if root.exists():
        for job_dir in sorted(root.glob("job-*")):
            if job_dir.is_dir():
                jobs.append(load_latest_job_state(job_dir))

    Path(output_file).write_text(json.dumps({"jobs": jobs}, indent=2), encoding="utf-8")


if __name__ == "__main__":
    generate_dashboard_data("/tmp/devagent-artifacts", "ui/jobs.json")
