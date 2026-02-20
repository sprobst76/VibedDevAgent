"""Minimal Matrix client for sync and room messaging."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib import parse, request


class MatrixApiError(RuntimeError):
    """Raised on HTTP/API failures."""


@dataclass(frozen=True)
class MatrixSyncResult:
    next_batch: str
    payload: dict[str, Any]


class MatrixClient:
    def __init__(self, homeserver_url: str, access_token: str) -> None:
        self.homeserver_url = homeserver_url.rstrip("/")
        self.access_token = access_token

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, str] | None = None,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = f"{self.homeserver_url}{path}"
        if query:
            url = f"{url}?{parse.urlencode(query)}"

        data: bytes | None = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")

        req = request.Request(
            url,
            method=method,
            data=data,
            headers={
                "Authorization": f"Bearer {self.access_token}",
                "Content-Type": "application/json",
                "User-Agent": "devagent/0.1",
            },
        )

        try:
            with request.urlopen(req, timeout=40) as resp:  # noqa: S310 - expected matrix URL
                raw = resp.read().decode("utf-8")
                if not raw:
                    return {}
                return json.loads(raw)
        except Exception as exc:  # noqa: BLE001
            raise MatrixApiError(f"matrix request failed: {exc}") from exc

    def sync(self, *, since: str | None, timeout_ms: int = 30000) -> MatrixSyncResult:
        query: dict[str, str] = {"timeout": str(timeout_ms)}
        if since:
            query["since"] = since

        payload = self._request_json("GET", "/_matrix/client/v3/sync", query=query)
        next_batch = str(payload.get("next_batch", ""))
        if not next_batch:
            raise MatrixApiError("sync response missing next_batch")
        return MatrixSyncResult(next_batch=next_batch, payload=payload)

    def send_message(self, *, room_id: str, body: str, msgtype: str = "m.text") -> dict[str, Any]:
        txn_id = str(abs(hash((room_id, body))))
        return self._request_json(
            "PUT",
            f"/_matrix/client/v3/rooms/{parse.quote(room_id, safe='')}/send/m.room.message/{txn_id}",
            body={"msgtype": msgtype, "body": body},
        )

    def send_notice(self, *, room_id: str, body: str) -> dict[str, Any]:
        return self.send_message(room_id=room_id, body=body, msgtype="m.notice")

    def send_event(self, *, room_id: str, event_type: str, content: dict[str, Any], txn_id: str) -> dict[str, Any]:
        return self._request_json(
            "PUT",
            f"/_matrix/client/v3/rooms/{parse.quote(room_id, safe='')}/send/{parse.quote(event_type, safe='')}/{parse.quote(txn_id, safe='')}",
            body=content,
        )

    def send_reaction(self, *, room_id: str, event_id: str, key: str, txn_id: str) -> dict[str, Any]:
        return self.send_event(
            room_id=room_id,
            event_type="m.reaction",
            txn_id=txn_id,
            content={
                "m.relates_to": {
                    "rel_type": "m.annotation",
                    "event_id": event_id,
                    "key": key,
                }
            },
        )

    def get_event(self, *, room_id: str, event_id: str) -> dict[str, Any]:
        return self._request_json(
            "GET",
            f"/_matrix/client/v3/rooms/{parse.quote(room_id, safe='')}/event/{parse.quote(event_id, safe='')}",
        )

    def get_room_state(self, *, room_id: str, event_type: str, state_key: str = "") -> dict[str, Any]:
        return self._request_json(
            "GET",
            f"/_matrix/client/v3/rooms/{parse.quote(room_id, safe='')}/state/{parse.quote(event_type, safe='')}/{parse.quote(state_key, safe='')}",
        )
