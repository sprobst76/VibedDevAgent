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


class MatrixClientSendTests(unittest.TestCase):
    @patch("urllib.request.urlopen")
    def test_send_message_uses_put_method(self, urlopen_mock) -> None:
        urlopen_mock.return_value = _DummyResponse('{"event_id":"$m1"}')
        client = MatrixClient("https://matrix.org", "token")
        client.send_message(room_id="!abc:matrix.org", body="hello")
        req = urlopen_mock.call_args[0][0]
        self.assertEqual(req.get_method(), "PUT")
        self.assertIn("/send/m.room.message/", req.full_url)

    @patch("urllib.request.urlopen")
    def test_send_notice_uses_m_notice_msgtype(self, urlopen_mock) -> None:
        urlopen_mock.return_value = _DummyResponse('{"event_id":"$n1"}')
        client = MatrixClient("https://matrix.org", "token")
        client.send_notice(room_id="!abc:matrix.org", body="notice!")
        req = urlopen_mock.call_args[0][0]
        import json
        body = json.loads(req.data.decode())
        self.assertEqual(body["msgtype"], "m.notice")
        self.assertEqual(body["body"], "notice!")

    @patch("urllib.request.urlopen")
    def test_create_room_posts_correct_body(self, urlopen_mock) -> None:
        urlopen_mock.return_value = _DummyResponse('{"room_id":"!new:matrix.org"}')
        client = MatrixClient("https://matrix.org", "token")
        room_id = client.create_room(
            name="Test Room",
            topic="A test topic",
            invite=["@alice:matrix.org"],
        )
        self.assertEqual(room_id, "!new:matrix.org")
        req = urlopen_mock.call_args[0][0]
        import json
        body = json.loads(req.data.decode())
        self.assertEqual(body["name"], "Test Room")
        self.assertEqual(body["topic"], "A test topic")
        self.assertIn("@alice:matrix.org", body["invite"])
        self.assertEqual(body["preset"], "private_chat")

    @patch("urllib.request.urlopen")
    def test_send_reaction_has_correct_relates_to(self, urlopen_mock) -> None:
        urlopen_mock.return_value = _DummyResponse('{"event_id":"$r1"}')
        client = MatrixClient("https://matrix.org", "token")
        client.send_reaction(
            room_id="!abc:matrix.org",
            event_id="$target_evt",
            key="✅",
            txn_id="txn-42",
        )
        req = urlopen_mock.call_args[0][0]
        import json
        body = json.loads(req.data.decode())
        rel = body["m.relates_to"]
        self.assertEqual(rel["rel_type"], "m.annotation")
        self.assertEqual(rel["event_id"], "$target_evt")
        self.assertEqual(rel["key"], "✅")

    @patch("urllib.request.urlopen")
    def test_room_id_with_special_chars_url_encoded(self, urlopen_mock) -> None:
        """Room IDs like !abc:matrix.org must be percent-encoded in URLs."""
        urlopen_mock.return_value = _DummyResponse('{"event_id":"$1"}')
        client = MatrixClient("https://matrix.org", "token")
        client.send_message(room_id="!abc:matrix.org", body="hi")
        req = urlopen_mock.call_args[0][0]
        # ! → %21, : → %3A
        self.assertIn("%21abc%3Amatrix.org", req.full_url)
        self.assertNotIn("!abc:matrix.org", req.full_url)

    @patch("urllib.request.urlopen")
    def test_invite_posts_to_correct_path(self, urlopen_mock) -> None:
        urlopen_mock.return_value = _DummyResponse('{}')
        client = MatrixClient("https://matrix.org", "token")
        client.invite(room_id="!abc:matrix.org", user_id="@bob:matrix.org")
        req = urlopen_mock.call_args[0][0]
        self.assertIn("/invite", req.full_url)
        import json
        body = json.loads(req.data.decode())
        self.assertEqual(body["user_id"], "@bob:matrix.org")

    @patch("urllib.request.urlopen")
    def test_two_send_messages_have_different_txn_ids(self, urlopen_mock) -> None:
        urlopen_mock.return_value = _DummyResponse('{"event_id":"$1"}')
        client = MatrixClient("https://matrix.org", "token")
        client.send_message(room_id="!r:m.org", body="a")
        url1 = urlopen_mock.call_args[0][0].full_url
        client.send_message(room_id="!r:m.org", body="b")
        url2 = urlopen_mock.call_args[0][0].full_url
        # Transaction IDs are in the URL path — must differ
        self.assertNotEqual(url1, url2)


if __name__ == "__main__":
    unittest.main()
