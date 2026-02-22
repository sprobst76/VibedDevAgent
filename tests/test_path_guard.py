"""Tests for core.path_guard — path/name validation utilities."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from core.path_guard import PathGuardError, safe_room_id, validate_project_name, validate_project_path


# ── validate_project_name ─────────────────────────────────────────────────────

class ValidateProjectNameTests(unittest.TestCase):
    # ── Valid names ───────────────────────────────────────────────────────────

    def test_simple_alpha(self):
        self.assertEqual(validate_project_name("myproject"), "myproject")

    def test_starts_with_digit(self):
        self.assertEqual(validate_project_name("2fast"), "2fast")

    def test_hyphens_underscores_dots(self):
        self.assertEqual(validate_project_name("my-project_v1.0"), "my-project_v1.0")

    def test_mixed_case(self):
        self.assertEqual(validate_project_name("MyProject"), "MyProject")

    def test_leading_trailing_whitespace_stripped(self):
        self.assertEqual(validate_project_name("  hello  "), "hello")

    def test_exactly_128_chars_after_first(self):
        # first char + 127 more = 128 total → valid
        name = "a" + "b" * 127
        self.assertEqual(validate_project_name(name), name)

    # ── Invalid names ─────────────────────────────────────────────────────────

    def test_empty_raises(self):
        with self.assertRaises(PathGuardError):
            validate_project_name("")

    def test_whitespace_only_raises(self):
        with self.assertRaises(PathGuardError):
            validate_project_name("   ")

    def test_path_separator_slash_raises(self):
        with self.assertRaises(PathGuardError):
            validate_project_name("foo/bar")

    def test_path_separator_backslash_raises(self):
        with self.assertRaises(PathGuardError):
            validate_project_name("foo\\bar")

    def test_dotdot_raises(self):
        with self.assertRaises(PathGuardError):
            validate_project_name("../etc/passwd")

    def test_space_in_name_raises(self):
        with self.assertRaises(PathGuardError):
            validate_project_name("my project")

    def test_semicolon_raises(self):
        with self.assertRaises(PathGuardError):
            validate_project_name("proj;rm -rf /")

    def test_dollar_sign_raises(self):
        with self.assertRaises(PathGuardError):
            validate_project_name("proj$var")

    def test_starts_with_dot_raises(self):
        with self.assertRaises(PathGuardError):
            validate_project_name(".hidden")

    def test_starts_with_hyphen_raises(self):
        with self.assertRaises(PathGuardError):
            validate_project_name("-bad")

    def test_too_long_raises(self):
        # 129 characters total — first char + 128 more
        name = "a" + "b" * 128
        with self.assertRaises(PathGuardError):
            validate_project_name(name)

    def test_ampersand_raises(self):
        with self.assertRaises(PathGuardError):
            validate_project_name("foo&bar")

    def test_backtick_raises(self):
        with self.assertRaises(PathGuardError):
            validate_project_name("foo`id`")

    def test_newline_raises(self):
        with self.assertRaises(PathGuardError):
            validate_project_name("foo\nbar")


# ── validate_project_path ─────────────────────────────────────────────────────

class ValidateProjectPathTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def test_path_inside_root_accepted(self):
        p = str(Path(self.root) / "myproject")
        result = validate_project_path(p, [self.root])
        self.assertTrue(result.startswith(self.root))

    def test_root_itself_accepted(self):
        result = validate_project_path(self.root, [self.root])
        self.assertEqual(result, str(Path(self.root).resolve()))

    def test_nested_path_accepted(self):
        p = str(Path(self.root) / "a" / "b" / "c")
        result = validate_project_path(p, [self.root])
        self.assertIn(self.root, result)

    def test_traversal_dotdot_rejected(self):
        p = str(Path(self.root) / ".." / "escape")
        with self.assertRaises(PathGuardError):
            validate_project_path(p, [self.root])

    def test_absolute_outside_root_rejected(self):
        with self.assertRaises(PathGuardError):
            validate_project_path("/etc/passwd", [self.root])

    def test_empty_path_raises(self):
        with self.assertRaises(PathGuardError):
            validate_project_path("", [self.root])

    def test_whitespace_only_path_raises(self):
        with self.assertRaises(PathGuardError):
            validate_project_path("   ", [self.root])

    def test_multiple_allowed_roots(self):
        with tempfile.TemporaryDirectory() as root2:
            p = str(Path(root2) / "project")
            result = validate_project_path(p, [self.root, root2])
            self.assertIn(root2, result)

    def test_none_of_multiple_roots_matches_raises(self):
        with tempfile.TemporaryDirectory() as root2:
            with self.assertRaises(PathGuardError):
                validate_project_path("/tmp/sneaky", [self.root, root2])

    def test_returns_resolved_canonical_path(self):
        """resolve() should collapse symlinks/.. and return canonical path."""
        p = str(Path(self.root) / "x" / ".." / "y")
        result = validate_project_path(p, [self.root])
        self.assertNotIn("..", result)

    def test_nonexistent_path_inside_root_ok(self):
        """Path doesn't need to exist yet — only the root must exist."""
        p = str(Path(self.root) / "future_dir" / "project")
        result = validate_project_path(p, [self.root])
        self.assertIn("future_dir", result)


# ── safe_room_id ──────────────────────────────────────────────────────────────

class SafeRoomIdTests(unittest.TestCase):
    # ── Valid room IDs ────────────────────────────────────────────────────────

    def test_standard_room_id(self):
        rid = "!abcXYZ123:matrix.org"
        self.assertEqual(safe_room_id(rid), rid)

    def test_room_id_with_underscores_and_dots(self):
        rid = "!room_name.sub:homeserver.example.com"
        self.assertEqual(safe_room_id(rid), rid)

    def test_room_id_leading_trailing_whitespace_stripped(self):
        self.assertEqual(safe_room_id("  !abc:matrix.org  "), "!abc:matrix.org")

    def test_room_id_subdomain_server(self):
        self.assertEqual(safe_room_id("!x:sub.domain.example"), "!x:sub.domain.example")

    def test_room_id_numeric_localpart(self):
        self.assertEqual(safe_room_id("!12345:matrix.org"), "!12345:matrix.org")

    # ── Invalid room IDs ──────────────────────────────────────────────────────

    def test_empty_raises(self):
        with self.assertRaises(PathGuardError):
            safe_room_id("")

    def test_no_exclamation_mark_raises(self):
        with self.assertRaises(PathGuardError):
            safe_room_id("abc:matrix.org")

    def test_no_server_part_raises(self):
        with self.assertRaises(PathGuardError):
            safe_room_id("!abcdef")

    def test_no_localpart_raises(self):
        with self.assertRaises(PathGuardError):
            safe_room_id("!:matrix.org")

    def test_at_sign_format_raises(self):
        """User IDs (@user:server) are not room IDs."""
        with self.assertRaises(PathGuardError):
            safe_room_id("@user:matrix.org")

    def test_hash_alias_format_raises(self):
        """Room aliases (#alias:server) are not raw room IDs."""
        with self.assertRaises(PathGuardError):
            safe_room_id("#general:matrix.org")

    def test_injection_in_room_id_raises(self):
        with self.assertRaises(PathGuardError):
            safe_room_id("!abc:mat\nrix.org")

    def test_space_in_room_id_raises(self):
        with self.assertRaises(PathGuardError):
            safe_room_id("!abc def:matrix.org")


if __name__ == "__main__":
    unittest.main()
