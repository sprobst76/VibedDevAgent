#!/usr/bin/env python3
"""Tail Matrix room events in terminal (independent of Element UI)."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from adapters.matrix.client import MatrixClient


def _ts_ms_to_iso(ts: int | None) -> str:
    if ts is None:
        return ""
    return datetime.fromtimestamp(ts / 1000, tz=timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _event_line(event: dict[str, Any]) -> str:
    event_id = str(event.get("event_id", ""))
    etype = str(event.get("type", ""))
    sender = str(event.get("sender", ""))
    ts = _ts_ms_to_iso(event.get("origin_server_ts") if isinstance(event.get("origin_server_ts"), int) else None)

    if etype == "m.room.message":
        content = event.get("content", {})
        body = content.get("body", "") if isinstance(content, dict) else ""
        return f"[{ts}] {event_id} {sender} m.room.message: {body}"

    if etype == "m.reaction":
        content = event.get("content", {})
        relates = content.get("m.relates_to", {}) if isinstance(content, dict) else {}
        key = relates.get("key", "") if isinstance(relates, dict) else ""
        target = relates.get("event_id", "") if isinstance(relates, dict) else ""
        return f"[{ts}] {event_id} {sender} m.reaction: {key} -> {target}"

    if etype == "devagent.jobcard":
        return f"[{ts}] {event_id} {sender} devagent.jobcard"

    return f"[{ts}] {event_id} {sender} {etype}"


def main() -> None:
    parser = argparse.ArgumentParser(description="Tail Matrix room events")
    parser.add_argument("--room-id", default=os.getenv("MATRIX_ROOM_ID", ""))
    parser.add_argument("--timeout-ms", type=int, default=30000)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--raw", action="store_true", help="Print full raw event JSON")
    args = parser.parse_args()

    homeserver_url = os.getenv("MATRIX_HOMESERVER_URL", "").strip()
    access_token = os.getenv("MATRIX_ACCESS_TOKEN", "").strip()
    room_id = args.room_id.strip()

    if not homeserver_url or not access_token or not room_id:
        raise SystemExit("Missing MATRIX_HOMESERVER_URL, MATRIX_ACCESS_TOKEN or room_id")

    client = MatrixClient(homeserver_url, access_token)
    since: str | None = None

    while True:
        res = client.sync(since=since, timeout_ms=args.timeout_ms)
        since = res.next_batch

        room = res.payload.get("rooms", {}).get("join", {}).get(room_id, {})
        timeline = room.get("timeline", {}) if isinstance(room, dict) else {}
        events = timeline.get("events", []) if isinstance(timeline, dict) else []
        if isinstance(events, list):
            for event in events:
                if not isinstance(event, dict):
                    continue
                if args.raw:
                    print(json.dumps(event, ensure_ascii=False))
                else:
                    print(_event_line(event))

        if args.once:
            break


if __name__ == "__main__":
    main()
