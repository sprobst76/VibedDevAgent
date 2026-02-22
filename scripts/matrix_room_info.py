#!/usr/bin/env python3
"""Show basic info for configured Matrix room."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from adapters.matrix.client import MatrixApiError, MatrixClient  # noqa: E402


def _safe_state(client: MatrixClient, room_id: str, event_type: str) -> dict[str, object]:
    try:
        return client.get_room_state(room_id=room_id, event_type=event_type)
    except MatrixApiError as exc:
        return {"error": str(exc)}


def main() -> None:
    parser = argparse.ArgumentParser(description="Show Matrix room info")
    parser.add_argument("--room-id", default=os.getenv("MATRIX_ROOM_ID", ""))
    args = parser.parse_args()

    homeserver_url = os.getenv("MATRIX_HOMESERVER_URL", "").strip()
    access_token = os.getenv("MATRIX_ACCESS_TOKEN", "").strip()
    room_id = args.room_id.strip()

    if not homeserver_url or not access_token or not room_id:
        raise SystemExit("Missing MATRIX_HOMESERVER_URL, MATRIX_ACCESS_TOKEN or room_id")

    client = MatrixClient(homeserver_url, access_token)
    out = {
        "room_id": room_id,
        "name": _safe_state(client, room_id, "m.room.name"),
        "topic": _safe_state(client, room_id, "m.room.topic"),
        "canonical_alias": _safe_state(client, room_id, "m.room.canonical_alias"),
        "encryption": _safe_state(client, room_id, "m.room.encryption"),
    }
    print(json.dumps(out, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
