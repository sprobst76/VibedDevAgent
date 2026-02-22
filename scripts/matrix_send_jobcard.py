#!/usr/bin/env python3
"""Send a devagent.jobcard event into a Matrix room."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

# Allow execution from any cwd without requiring external PYTHONPATH setup.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from adapters.matrix.client import MatrixClient  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Send Matrix jobcard event")
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--branch", default="main")
    parser.add_argument("--command", required=True)
    parser.add_argument("--requested-by", required=True)
    parser.add_argument(
        "--mode",
        choices=["notice", "text", "event", "both"],
        default="text",
        help="text=sichtbar in Element, notice=botsignal, event=custom event, both=event+text",
    )
    args = parser.parse_args()

    homeserver_url = os.getenv("MATRIX_HOMESERVER_URL", "").strip()
    access_token = os.getenv("MATRIX_ACCESS_TOKEN", "").strip()
    room_id = os.getenv("MATRIX_ROOM_ID", "").strip()

    if not homeserver_url or not access_token or not room_id:
        raise SystemExit("Missing MATRIX_HOMESERVER_URL, MATRIX_ACCESS_TOKEN or MATRIX_ROOM_ID")

    client = MatrixClient(homeserver_url, access_token)
    content = {
        "job_id": args.job_id,
        "repo": args.repo,
        "branch": args.branch,
        "command": args.command,
        "requested_by": args.requested_by,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    results: dict[str, object] = {}

    if args.mode in {"event", "both"}:
        txn_id = f"devagent-jobcard-{args.job_id}-{int(time.time())}"
        results["event"] = client.send_event(
            room_id=room_id,
            event_type="devagent.jobcard",
            content=content,
            txn_id=txn_id,
        )

    if args.mode in {"notice", "both"}:
        body = "DEVAGENT_JOBCARD " + json.dumps(content, ensure_ascii=True)
        results["notice"] = client.send_notice(room_id=room_id, body=body)
    if args.mode == "text":
        body = "DEVAGENT_JOBCARD " + json.dumps(content, ensure_ascii=True)
        results["text"] = client.send_message(room_id=room_id, body=body, msgtype="m.text")
    if args.mode == "both":
        body = "DEVAGENT_JOBCARD " + json.dumps(content, ensure_ascii=True)
        results["text"] = client.send_message(room_id=room_id, body=body, msgtype="m.text")

    print(json.dumps(results, ensure_ascii=True))


if __name__ == "__main__":
    main()
