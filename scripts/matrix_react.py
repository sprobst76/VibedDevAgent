#!/usr/bin/env python3
"""Send a Matrix reaction to an existing event."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from adapters.matrix.client import MatrixClient


def main() -> None:
    parser = argparse.ArgumentParser(description="Send Matrix reaction")
    parser.add_argument("--event-id", required=True)
    parser.add_argument("--key", required=True, help="Emoji key, e.g. ✅ or 🛑")
    parser.add_argument("--room-id", default=os.getenv("MATRIX_ROOM_ID", ""))
    args = parser.parse_args()

    homeserver_url = os.getenv("MATRIX_HOMESERVER_URL", "").strip()
    access_token = os.getenv("MATRIX_ACCESS_TOKEN", "").strip()
    room_id = args.room_id.strip()

    if not homeserver_url or not access_token or not room_id:
        raise SystemExit("Missing MATRIX_HOMESERVER_URL, MATRIX_ACCESS_TOKEN or room_id")

    txn_id = f"devagent-react-{int(time.time() * 1000)}"
    client = MatrixClient(homeserver_url, access_token)
    out = client.send_reaction(room_id=room_id, event_id=args.event_id, key=args.key, txn_id=txn_id)
    print(json.dumps(out, ensure_ascii=True))


if __name__ == "__main__":
    main()
