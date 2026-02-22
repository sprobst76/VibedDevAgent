"""Minimal Matrix client for sync and room messaging."""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib import parse, request, error as urllib_error

log = logging.getLogger("devagent.matrix_client")


class MatrixApiError(RuntimeError):
    """Raised on HTTP/API failures."""


class MatrixAuthError(MatrixApiError):
    """Raised on 401 Unauthorized — token expired or invalid."""


@dataclass(frozen=True)
class MatrixSyncResult:
    next_batch: str
    payload: dict[str, Any]


class MatrixClient:
    def __init__(self, homeserver_url: str, access_token: str) -> None:
        self.homeserver_url = homeserver_url.rstrip("/")
        self.access_token   = access_token
        # Optional: credentials for auto-relogin on 401
        self._login_user:     str | None = None
        self._login_password: str | None = None
        self._env_file:       str | None = None

    def set_relogin_credentials(
        self, user: str, password: str, env_file: str | None = None
    ) -> None:
        """Configure automatic re-login on 401. env_file path to update token in."""
        self._login_user     = user
        self._login_password = password
        self._env_file       = env_file

    # ── Internal HTTP ─────────────────────────────────────────────────────────

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, str] | None = None,
        body: dict[str, Any] | None = None,
        _retry_auth: bool = True,
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
                "Content-Type":  "application/json",
                "User-Agent":    "devagent/0.1",
            },
        )

        try:
            with request.urlopen(req, timeout=40) as resp:  # noqa: S310
                raw = resp.read().decode("utf-8")
                if not raw:
                    return {}
                return json.loads(raw)
        except urllib_error.HTTPError as exc:
            if exc.code == 401 and _retry_auth and self._login_user:
                log.warning("Matrix 401 — attempting auto re-login as %s", self._login_user)
                self._relogin()
                return self._request_json(method, path, query=query, body=body, _retry_auth=False)
            if exc.code == 401:
                raise MatrixAuthError(f"matrix request failed: {exc}") from exc
            raise MatrixApiError(f"matrix request failed: {exc}") from exc
        except Exception as exc:  # noqa: BLE001
            raise MatrixApiError(f"matrix request failed: {exc}") from exc

    def _relogin(self) -> None:
        """Re-login with stored credentials and update access_token."""
        url  = f"{self.homeserver_url}/_matrix/client/v3/login"
        data = json.dumps({
            "type":     "m.login.password",
            "user":     self._login_user,
            "password": self._login_password,
        }).encode("utf-8")
        req = request.Request(url, method="POST", data=data, headers={
            "Content-Type": "application/json",
            "User-Agent":   "devagent/0.1",
        })
        try:
            with request.urlopen(req, timeout=30) as resp:  # noqa: S310
                result = json.loads(resp.read().decode("utf-8"))
            new_token = result["access_token"]
            self.access_token = new_token
            log.info("auto re-login successful, new token: %s…", new_token[:16])
            self._persist_token(new_token)
        except Exception as exc:
            raise MatrixApiError(f"auto re-login failed: {exc}") from exc

    def _persist_token(self, token: str) -> None:
        """Write new token back to .env file if configured."""
        if not self._env_file:
            return
        path = Path(self._env_file)
        if not path.exists():
            return
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
            updated = []
            found = False
            for line in lines:
                if line.startswith("MATRIX_ACCESS_TOKEN="):
                    updated.append(f"MATRIX_ACCESS_TOKEN={token}")
                    found = True
                else:
                    updated.append(line)
            if not found:
                updated.append(f"MATRIX_ACCESS_TOKEN={token}")
            path.write_text("\n".join(updated) + "\n", encoding="utf-8")
            log.info("updated MATRIX_ACCESS_TOKEN in %s", self._env_file)
        except Exception as exc:
            log.warning("could not persist token to %s: %s", self._env_file, exc)

    # ── Public API ────────────────────────────────────────────────────────────

    def sync(self, *, since: str | None, timeout_ms: int = 30000) -> MatrixSyncResult:
        query: dict[str, str] = {"timeout": str(timeout_ms)}
        if since:
            query["since"] = since
        payload    = self._request_json("GET", "/_matrix/client/v3/sync", query=query)
        next_batch = str(payload.get("next_batch", ""))
        if not next_batch:
            raise MatrixApiError("sync response missing next_batch")
        return MatrixSyncResult(next_batch=next_batch, payload=payload)

    def send_message(self, *, room_id: str, body: str, msgtype: str = "m.text") -> dict[str, Any]:
        txn_id = f"{int(time.time_ns())}"
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

    def create_room(self, *, name: str, topic: str = "", invite: list[str] | None = None) -> str:
        """Create an unencrypted private room. Returns room_id."""
        resp = self._request_json(
            "POST",
            "/_matrix/client/v3/createRoom",
            body={
                "name":          name,
                "preset":        "private_chat",
                "initial_state": [],
                "topic":         topic,
                "invite":        invite or [],
            },
        )
        return resp["room_id"]

    def invite(self, *, room_id: str, user_id: str) -> None:
        self._request_json(
            "POST",
            f"/_matrix/client/v3/rooms/{parse.quote(room_id, safe='')}/invite",
            body={"user_id": user_id},
        )

    def send_reaction(self, *, room_id: str, event_id: str, key: str, txn_id: str) -> dict[str, Any]:
        return self.send_event(
            room_id=room_id, event_type="m.reaction", txn_id=txn_id,
            content={"m.relates_to": {"rel_type": "m.annotation", "event_id": event_id, "key": key}},
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

    def get_joined_rooms(self) -> list[str]:
        resp = self._request_json("GET", "/_matrix/client/v3/joined_rooms")
        return resp.get("joined_rooms", [])

    def get_room_name(self, *, room_id: str) -> str:
        try:
            resp = self.get_room_state(room_id=room_id, event_type="m.room.name")
            return resp.get("name", "")
        except MatrixApiError:
            return ""
