"""Tests for ops/cron/matrix_relogin.py"""

from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Load the script as a module (it has no package, lives under ops/cron/).
_SCRIPT = Path(__file__).parent.parent / "ops" / "cron" / "matrix_relogin.py"
_spec = importlib.util.spec_from_file_location("matrix_relogin", _SCRIPT)
_mod  = importlib.util.module_from_spec(_spec)   # type: ignore[arg-type]
_spec.loader.exec_module(_mod)                    # type: ignore[union-attr]

_parse_env_file  = _mod._parse_env_file
_update_env_file = _mod._update_env_file
_matrix_login    = _mod._matrix_login
main             = _mod.main


# ── _parse_env_file ───────────────────────────────────────────────────────────

class ParseEnvFileTests(unittest.TestCase):

    def _write(self, content: str) -> Path:
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".env", delete=False)
        f.write(content)
        f.flush()
        return Path(f.name)

    def test_basic_key_value(self):
        p = self._write("FOO=bar\nBAZ=qux\n")
        env = _parse_env_file(p)
        self.assertEqual(env["FOO"], "bar")
        self.assertEqual(env["BAZ"], "qux")

    def test_ignores_comments(self):
        p = self._write("# comment\nFOO=bar\n")
        env = _parse_env_file(p)
        self.assertNotIn("# comment", env)
        self.assertEqual(env["FOO"], "bar")

    def test_ignores_blank_lines(self):
        p = self._write("\n\nFOO=bar\n\n")
        env = _parse_env_file(p)
        self.assertEqual(env["FOO"], "bar")

    def test_strips_double_quotes(self):
        p = self._write('FOO="hello world"\n')
        env = _parse_env_file(p)
        self.assertEqual(env["FOO"], "hello world")

    def test_strips_single_quotes(self):
        p = self._write("FOO='hello world'\n")
        env = _parse_env_file(p)
        self.assertEqual(env["FOO"], "hello world")

    def test_value_with_equals_sign(self):
        # Token values may contain '='
        p = self._write("TOKEN=abc=def==\n")
        env = _parse_env_file(p)
        self.assertEqual(env["TOKEN"], "abc=def==")

    def test_inline_comment_stripped(self):
        p = self._write("FOO=bar # this is a comment\n")
        env = _parse_env_file(p)
        self.assertEqual(env["FOO"], "bar")

    def test_matrix_access_token_preserved(self):
        # Simulate a real-world .env snippet
        content = (
            "MATRIX_HOMESERVER_URL=https://matrix.example.org\n"
            "MATRIX_ACCESS_TOKEN=syt_abc123XYZ\n"
            "MATRIX_USER_DEVAGENT=@bot:example.org\n"
            "MATRIX_PASSWORD_DEVAGENT=s3cr3t\n"
        )
        p = self._write(content)
        env = _parse_env_file(p)
        self.assertEqual(env["MATRIX_ACCESS_TOKEN"], "syt_abc123XYZ")
        self.assertEqual(env["MATRIX_HOMESERVER_URL"], "https://matrix.example.org")


# ── _update_env_file ──────────────────────────────────────────────────────────

class UpdateEnvFileTests(unittest.TestCase):

    def _env(self, content: str) -> Path:
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".env", delete=False)
        f.write(content)
        f.flush()
        return Path(f.name)

    def test_updates_existing_key(self):
        p = self._env("MATRIX_ACCESS_TOKEN=old_token\nOTHER=value\n")
        _update_env_file(p, "MATRIX_ACCESS_TOKEN", "new_token")
        content = p.read_text()
        self.assertIn("MATRIX_ACCESS_TOKEN=new_token", content)
        self.assertNotIn("old_token", content)
        self.assertIn("OTHER=value", content)

    def test_adds_missing_key(self):
        p = self._env("OTHER=value\n")
        _update_env_file(p, "MATRIX_ACCESS_TOKEN", "new_token")
        content = p.read_text()
        self.assertIn("MATRIX_ACCESS_TOKEN=new_token", content)
        self.assertIn("OTHER=value", content)

    def test_preserves_other_lines(self):
        p = self._env(
            "# comment\n"
            "MATRIX_ACCESS_TOKEN=old\n"
            "FOO=bar\n"
        )
        _update_env_file(p, "MATRIX_ACCESS_TOKEN", "new")
        lines = p.read_text().splitlines()
        self.assertIn("# comment", lines)
        self.assertIn("FOO=bar", lines)

    def test_atomic_write(self):
        """File must be fully written (no partial state)."""
        p = self._env("MATRIX_ACCESS_TOKEN=old\n")
        _update_env_file(p, "MATRIX_ACCESS_TOKEN", "x" * 1000)
        content = p.read_text()
        self.assertIn("MATRIX_ACCESS_TOKEN=" + "x" * 1000, content)


# ── _matrix_login ─────────────────────────────────────────────────────────────

class MatrixLoginTests(unittest.TestCase):

    def _mock_response(self, body: str, status: int = 200):
        resp = MagicMock()
        resp.__enter__ = lambda s: s
        resp.__exit__ = MagicMock(return_value=False)
        resp.read.return_value = body.encode("utf-8")
        resp.status = status
        return resp

    @patch("urllib.request.urlopen")
    def test_returns_access_token(self, mock_urlopen):
        mock_urlopen.return_value = self._mock_response(
            '{"access_token": "syt_newtoken123"}'
        )
        token = _matrix_login("https://matrix.example.org", "@bot:example.org", "pass")
        self.assertEqual(token, "syt_newtoken123")

    @patch("urllib.request.urlopen")
    def test_raises_on_missing_token(self, mock_urlopen):
        mock_urlopen.return_value = self._mock_response('{"error": "wrong password"}')
        with self.assertRaises(RuntimeError):
            _matrix_login("https://matrix.example.org", "@bot:example.org", "wrong")

    @patch("urllib.request.urlopen")
    def test_raises_on_http_error(self, mock_urlopen):
        from urllib.error import HTTPError
        err = HTTPError(url="http://x", code=403, msg="Forbidden", hdrs=None, fp=None)
        mock_urlopen.side_effect = err
        with self.assertRaises(RuntimeError):
            _matrix_login("https://matrix.example.org", "@bot:example.org", "pass")


# ── main() ────────────────────────────────────────────────────────────────────

class MainTests(unittest.TestCase):

    def _write_env(self, tmpdir: str, extra: str = "") -> str:
        path = str(Path(tmpdir) / ".env")
        Path(path).write_text(
            "MATRIX_HOMESERVER_URL=https://matrix.example.org\n"
            "MATRIX_USER_DEVAGENT=@bot:example.org\n"
            "MATRIX_PASSWORD_DEVAGENT=s3cr3t\n"
            "MATRIX_ACCESS_TOKEN=old_token\n"
            + extra,
            encoding="utf-8",
        )
        return path

    def test_missing_env_file_returns_1(self):
        with patch.object(sys, "argv", ["matrix_relogin.py", "/nonexistent/.env"]):
            rc = main()
        self.assertEqual(rc, 1)

    def test_missing_variables_returns_1(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = str(Path(tmpdir) / ".env")
            Path(path).write_text("FOO=bar\n", encoding="utf-8")
            with patch.object(sys, "argv", ["matrix_relogin.py", path]):
                rc = main()
        self.assertEqual(rc, 1)

    @patch("urllib.request.urlopen")
    def test_success_updates_token(self, mock_urlopen):
        resp = MagicMock()
        resp.__enter__ = lambda s: s
        resp.__exit__ = MagicMock(return_value=False)
        resp.read.return_value = b'{"access_token": "syt_brand_new"}'
        mock_urlopen.return_value = resp

        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_env(tmpdir)
            with patch.object(sys, "argv", ["matrix_relogin.py", path]):
                rc = main()
            content = Path(path).read_text()

        self.assertEqual(rc, 0)
        self.assertIn("MATRIX_ACCESS_TOKEN=syt_brand_new", content)
        self.assertNotIn("old_token", content)

    @patch("urllib.request.urlopen")
    def test_login_failure_returns_2(self, mock_urlopen):
        from urllib.error import HTTPError
        mock_urlopen.side_effect = HTTPError(
            url="http://x", code=403, msg="Forbidden", hdrs=None, fp=None
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_env(tmpdir)
            with patch.object(sys, "argv", ["matrix_relogin.py", path]):
                rc = main()
        self.assertEqual(rc, 2)


if __name__ == "__main__":
    unittest.main()
