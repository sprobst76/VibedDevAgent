from __future__ import annotations

import unittest
from unittest.mock import patch

from adapters.matrix.client import MatrixClient


class _DummyResponse:
    def __init__(self, payload: str) -> None:
        self.payload = payload

    def __enter__(self) -> "_DummyResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # type: ignore[no-untyped-def]
        return None

    def read(self) -> bytes:
        return self.payload.encode("utf-8")


class MatrixClientTests(unittest.TestCase):
    @patch("urllib.request.urlopen")
    def test_sync_parses_next_batch(self, urlopen_mock) -> None:  # type: ignore[no-untyped-def]
        urlopen_mock.return_value = _DummyResponse('{"next_batch":"s123","rooms":{"join":{}}}')
        client = MatrixClient("https://matrix.org", "token")

        res = client.sync(since="s122", timeout_ms=15000)

        self.assertEqual(res.next_batch, "s123")
        req = urlopen_mock.call_args[0][0]
        self.assertIn("/_matrix/client/v3/sync", req.full_url)
        self.assertIn("since=s122", req.full_url)
        self.assertIn("timeout=15000", req.full_url)

    @patch("urllib.request.urlopen")
    def test_send_event_uses_expected_path(self, urlopen_mock) -> None:  # type: ignore[no-untyped-def]
        urlopen_mock.return_value = _DummyResponse('{"event_id":"$1"}')
        client = MatrixClient("https://matrix.org", "token")

        out = client.send_event(
            room_id="!abc:matrix.org",
            event_type="devagent.jobcard",
            content={"k": "v"},
            txn_id="t1",
        )

        self.assertEqual(out["event_id"], "$1")
        req = urlopen_mock.call_args[0][0]
        self.assertIn("/rooms/%21abc%3Amatrix.org/send/devagent.jobcard/t1", req.full_url)

    @patch("urllib.request.urlopen")
    def test_get_event_uses_expected_path(self, urlopen_mock) -> None:  # type: ignore[no-untyped-def]
        urlopen_mock.return_value = _DummyResponse('{"event_id":"$2","type":"m.room.message"}')
        client = MatrixClient("https://matrix.org", "token")

        out = client.get_event(room_id="!abc:matrix.org", event_id="$2")

        self.assertEqual(out["event_id"], "$2")
        req = urlopen_mock.call_args[0][0]
        self.assertIn("/rooms/%21abc%3Amatrix.org/event/%242", req.full_url)

    @patch("urllib.request.urlopen")
    def test_get_room_state_uses_expected_path(self, urlopen_mock) -> None:  # type: ignore[no-untyped-def]
        urlopen_mock.return_value = _DummyResponse('{"name":"DevAgent Room"}')
        client = MatrixClient("https://matrix.org", "token")

        out = client.get_room_state(room_id="!abc:matrix.org", event_type="m.room.name")

        self.assertEqual(out["name"], "DevAgent Room")
        req = urlopen_mock.call_args[0][0]
        self.assertIn("/rooms/%21abc%3Amatrix.org/state/m.room.name/", req.full_url)


if __name__ == "__main__":
    unittest.main()
