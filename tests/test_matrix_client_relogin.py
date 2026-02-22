"""Tests for MatrixClient auto-relogin and token persistence (client.py)."""
from __future__ import annotations

import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, call, patch

from adapters.matrix.client import MatrixApiError, MatrixAuthError, MatrixClient


# ── Helpers ───────────────────────────────────────────────────────────────────

class _FakeResp:
    """Fake urllib response context manager."""

    def __init__(self, body: dict | str) -> None:
        if isinstance(body, dict):
            body = json.dumps(body)
        self._data = body.encode("utf-8")

    def __enter__(self) -> "_FakeResp":
        return self

    def __exit__(self, *_) -> None:
        pass

    def read(self) -> bytes:
        return self._data


def _http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(url="http://x", code=code, msg="err", hdrs=None, fp=None)


# ── _persist_token ────────────────────────────────────────────────────────────

class PersistTokenTests(unittest.TestCase):
    def test_updates_token_line_in_env_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = Path(tmp) / ".env"
            env.write_text(
                "MATRIX_HOMESERVER_URL=https://matrix.org\n"
                "MATRIX_ACCESS_TOKEN=old_token\n"
                "OTHER_VAR=keep\n",
                encoding="utf-8",
            )
            client = MatrixClient("https://matrix.org", "old_token")
            client.set_relogin_credentials("user", "pass", env_file=str(env))
            client._persist_token("new_token_abc123")

            lines = env.read_text(encoding="utf-8").splitlines()
            self.assertIn("MATRIX_ACCESS_TOKEN=new_token_abc123", lines)
            self.assertIn("OTHER_VAR=keep", lines)
            self.assertNotIn("MATRIX_ACCESS_TOKEN=old_token", lines)

    def test_no_env_file_configured_does_nothing(self) -> None:
        client = MatrixClient("https://matrix.org", "tok")
        # Should not raise even when _env_file is None
        client._persist_token("new_tok")

    def test_nonexistent_env_file_does_nothing(self) -> None:
        client = MatrixClient("https://matrix.org", "tok")
        client._env_file = "/nonexistent/path/.env"
        client._persist_token("new_tok")  # must not raise

    def test_token_written_without_trailing_newline_issues(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = Path(tmp) / ".env"
            env.write_text("MATRIX_ACCESS_TOKEN=old\n", encoding="utf-8")
            client = MatrixClient("https://matrix.org", "old")
            client._env_file = str(env)
            client._persist_token("fresh")
            content = env.read_text(encoding="utf-8")
            self.assertIn("MATRIX_ACCESS_TOKEN=fresh", content)

    def test_token_line_appended_when_not_present(self) -> None:
        """If MATRIX_ACCESS_TOKEN is not in .env yet, it must be appended."""
        with tempfile.TemporaryDirectory() as tmp:
            env = Path(tmp) / ".env"
            env.write_text("MATRIX_HOMESERVER_URL=https://matrix.org\nOTHER=val\n", encoding="utf-8")
            client = MatrixClient("https://matrix.org", "tok")
            client._env_file = str(env)
            client._persist_token("brand_new_token")
            content = env.read_text(encoding="utf-8")
            self.assertIn("MATRIX_ACCESS_TOKEN=brand_new_token", content)
            # Other vars must be preserved
            self.assertIn("MATRIX_HOMESERVER_URL=https://matrix.org", content)
            self.assertIn("OTHER=val", content)


# ── auto re-login on 401 ──────────────────────────────────────────────────────

class ReloginTests(unittest.TestCase):
    @patch("urllib.request.urlopen")
    def test_401_triggers_relogin_and_retries(self, urlopen_mock) -> None:
        """A 401 should cause a re-login, then the original request is retried."""
        login_resp  = _FakeResp({"access_token": "new_tok_999"})
        normal_resp = _FakeResp({"next_batch": "s1", "rooms": {"join": {}}})

        urlopen_mock.side_effect = [
            _http_error(401),   # first sync attempt → 401
            login_resp,         # re-login POST
            normal_resp,        # retry of the original sync
        ]

        client = MatrixClient("https://matrix.org", "expired_tok")
        client.set_relogin_credentials("@bot:matrix.org", "s3cr3t")

        result = client.sync(since=None, timeout_ms=1000)

        self.assertEqual(result.next_batch, "s1")
        self.assertEqual(client.access_token, "new_tok_999")
        self.assertEqual(urlopen_mock.call_count, 3)

    @patch("urllib.request.urlopen")
    def test_401_without_credentials_raises_auth_error(self, urlopen_mock) -> None:
        urlopen_mock.side_effect = _http_error(401)

        client = MatrixClient("https://matrix.org", "tok")
        with self.assertRaises(MatrixAuthError):
            client.sync(since=None, timeout_ms=1000)

    @patch("urllib.request.urlopen")
    def test_no_double_retry_on_second_401(self, urlopen_mock) -> None:
        """If the retry also gets a 401, raise MatrixAuthError (don't loop)."""
        login_resp = _FakeResp({"access_token": "tok2"})
        urlopen_mock.side_effect = [
            _http_error(401),   # first attempt
            login_resp,         # re-login succeeds
            _http_error(401),   # retry also 401
        ]

        client = MatrixClient("https://matrix.org", "old")
        client.set_relogin_credentials("@bot:matrix.org", "pass")

        with self.assertRaises(MatrixAuthError):
            client.sync(since=None, timeout_ms=1000)

    @patch("urllib.request.urlopen")
    def test_relogin_failure_raises_api_error(self, urlopen_mock) -> None:
        urlopen_mock.side_effect = [
            _http_error(401),           # first attempt
            _http_error(403),           # re-login POST fails
        ]

        client = MatrixClient("https://matrix.org", "old")
        client.set_relogin_credentials("@bot:matrix.org", "wrongpass")

        with self.assertRaises(MatrixApiError):
            client.sync(since=None, timeout_ms=1000)

    @patch("urllib.request.urlopen")
    def test_non_401_http_error_raises_api_error(self, urlopen_mock) -> None:
        urlopen_mock.side_effect = _http_error(500)
        client = MatrixClient("https://matrix.org", "tok")
        with self.assertRaises(MatrixApiError) as ctx:
            client.sync(since=None, timeout_ms=1000)
        self.assertNotIsInstance(ctx.exception, MatrixAuthError)

    @patch("urllib.request.urlopen")
    def test_relogin_updates_access_token_in_memory(self, urlopen_mock) -> None:
        login_resp  = _FakeResp({"access_token": "brand_new_token"})
        normal_resp = _FakeResp({"next_batch": "s2", "rooms": {"join": {}}})
        urlopen_mock.side_effect = [_http_error(401), login_resp, normal_resp]

        client = MatrixClient("https://matrix.org", "old_tok")
        client.set_relogin_credentials("@u:matrix.org", "pw")
        client.sync(since=None, timeout_ms=100)

        self.assertEqual(client.access_token, "brand_new_token")

    @patch("urllib.request.urlopen")
    def test_relogin_persists_token_to_env_file(self, urlopen_mock) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = Path(tmp) / ".env"
            env.write_text("MATRIX_ACCESS_TOKEN=old\n", encoding="utf-8")

            login_resp  = _FakeResp({"access_token": "persisted_token"})
            normal_resp = _FakeResp({"next_batch": "s3", "rooms": {"join": {}}})
            urlopen_mock.side_effect = [_http_error(401), login_resp, normal_resp]

            client = MatrixClient("https://matrix.org", "old")
            client.set_relogin_credentials("@u:matrix.org", "pw", env_file=str(env))
            client.sync(since=None, timeout_ms=100)

            content = env.read_text(encoding="utf-8")
            self.assertIn("MATRIX_ACCESS_TOKEN=persisted_token", content)


# ── _request_json edge cases ─────────────────────────────────────────────────

class RequestJsonEdgeCaseTests(unittest.TestCase):
    @patch("urllib.request.urlopen")
    def test_empty_response_returns_empty_dict(self, urlopen_mock) -> None:
        """An empty HTTP body (e.g., 204 No Content) must not crash."""
        resp = _FakeResp("")
        urlopen_mock.return_value = resp
        client = MatrixClient("https://matrix.org", "tok")
        result = client._request_json("GET", "/_matrix/client/v3/joined_rooms")
        self.assertEqual(result, {})

    @patch("urllib.request.urlopen")
    def test_sync_missing_next_batch_raises(self, urlopen_mock) -> None:
        urlopen_mock.return_value = _FakeResp({"rooms": {}})  # no next_batch key
        client = MatrixClient("https://matrix.org", "tok")
        with self.assertRaises(MatrixApiError):
            client.sync(since=None, timeout_ms=100)

    @patch("urllib.request.urlopen")
    def test_network_error_raises_api_error(self, urlopen_mock) -> None:
        urlopen_mock.side_effect = OSError("connection refused")
        client = MatrixClient("https://matrix.org", "tok")
        with self.assertRaises(MatrixApiError):
            client.sync(since=None, timeout_ms=100)

    @patch("urllib.request.urlopen")
    def test_authorization_header_sent(self, urlopen_mock) -> None:
        urlopen_mock.return_value = _FakeResp({"next_batch": "s1", "rooms": {"join": {}}})
        client = MatrixClient("https://matrix.org", "my_secret_token")
        client.sync(since=None, timeout_ms=1000)
        req = urlopen_mock.call_args[0][0]
        self.assertEqual(req.get_header("Authorization"), "Bearer my_secret_token")

    @patch("urllib.request.urlopen")
    def test_access_token_updated_after_relogin(self, urlopen_mock) -> None:
        """After relogin, subsequent requests use the new token."""
        login_resp  = _FakeResp({"access_token": "tok_v2"})
        sync_resp1  = _FakeResp({"next_batch": "s1", "rooms": {"join": {}}})
        sync_resp2  = _FakeResp({"next_batch": "s2", "rooms": {"join": {}}})
        urlopen_mock.side_effect = [_http_error(401), login_resp, sync_resp1, sync_resp2]

        client = MatrixClient("https://matrix.org", "tok_v1")
        client.set_relogin_credentials("@u:m.org", "pw")
        client.sync(since=None, timeout_ms=100)  # triggers relogin

        # Second sync should use the new token automatically
        client.sync(since="s1", timeout_ms=100)
        second_req = urlopen_mock.call_args[0][0]
        self.assertEqual(second_req.get_header("Authorization"), "Bearer tok_v2")


# ── get_joined_rooms / get_room_name ──────────────────────────────────────────

class JoinedRoomsTests(unittest.TestCase):
    @patch("urllib.request.urlopen")
    def test_get_joined_rooms_returns_list(self, urlopen_mock) -> None:
        urlopen_mock.return_value = _FakeResp(
            {"joined_rooms": ["!abc:matrix.org", "!def:matrix.org"]}
        )
        client = MatrixClient("https://matrix.org", "tok")
        rooms = client.get_joined_rooms()
        self.assertEqual(rooms, ["!abc:matrix.org", "!def:matrix.org"])

    @patch("urllib.request.urlopen")
    def test_get_joined_rooms_empty(self, urlopen_mock) -> None:
        urlopen_mock.return_value = _FakeResp({"joined_rooms": []})
        client = MatrixClient("https://matrix.org", "tok")
        self.assertEqual(client.get_joined_rooms(), [])

    @patch("urllib.request.urlopen")
    def test_get_room_name_success(self, urlopen_mock) -> None:
        urlopen_mock.return_value = _FakeResp({"name": "My Cool Room"})
        client = MatrixClient("https://matrix.org", "tok")
        name = client.get_room_name(room_id="!abc:matrix.org")
        self.assertEqual(name, "My Cool Room")

    @patch("urllib.request.urlopen")
    def test_get_room_name_api_error_returns_empty_string(self, urlopen_mock) -> None:
        urlopen_mock.side_effect = _http_error(404)
        client = MatrixClient("https://matrix.org", "tok")
        name = client.get_room_name(room_id="!abc:matrix.org")
        self.assertEqual(name, "")

    @patch("urllib.request.urlopen")
    def test_get_room_name_missing_key_returns_empty(self, urlopen_mock) -> None:
        urlopen_mock.return_value = _FakeResp({})  # no "name" key
        client = MatrixClient("https://matrix.org", "tok")
        name = client.get_room_name(room_id="!abc:matrix.org")
        self.assertEqual(name, "")


if __name__ == "__main__":
    unittest.main()
