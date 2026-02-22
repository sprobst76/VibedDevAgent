#!/usr/bin/env python3
"""Fetch one Matrix room event by event_id using env config."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from adapters.matrix.client import MatrixClient  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch Matrix event")
    parser.add_argument("--event-id", required=True)
    parser.add_argument("--room-id", default=os.getenv("MATRIX_ROOM_ID", ""))
    args = parser.parse_args()

    homeserver_url = os.getenv("MATRIX_HOMESERVER_URL", "").strip()
    access_token = os.getenv("MATRIX_ACCESS_TOKEN", "").strip()
    room_id = args.room_id.strip()

    if not homeserver_url or not access_token or not room_id:
        raise SystemExit("Missing MATRIX_HOMESERVER_URL, MATRIX_ACCESS_TOKEN or room_id")

    client = MatrixClient(homeserver_url, access_token)
    payload = client.get_event(room_id=room_id, event_id=args.event_id)
    print(json.dumps(payload, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
