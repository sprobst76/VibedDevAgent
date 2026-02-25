"""Integration tests that verify the *live system state* after running
setup_devagent_user.sh.

All tests are skipped automatically when the expected system paths are absent
(e.g. in CI or on a developer machine that has not run the setup script).
Running them requires no special privileges — they read publicly accessible
paths and invoke ``systemctl``/``getfacl`` as a normal user.
"""

from __future__ import annotations

import grp
import os
import pwd
import re
import stat
import subprocess
import unittest
from pathlib import Path

# ── Skip guard ────────────────────────────────────────────────────────────────

_SYSTEMD_CORE = Path("/etc/systemd/system/devagent.service")
_SYSTEMD_UI   = Path("/etc/systemd/system/devagent-ui.service")
_SRV          = Path("/srv/devagent")
_LOG          = Path("/var/log/devagent")
_CLAUDE_BIN   = Path("/usr/local/bin/claude")

_ON_SYSTEM = (
    _SRV.exists()
    and _SYSTEMD_CORE.exists()
    and _SYSTEMD_UI.exists()
)

_skip = unittest.skipUnless(
    _ON_SYSTEM,
    "System not configured — run ops/systemd/setup_devagent_user.sh first",
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _stat_mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def _owner(path: Path) -> tuple[str, str]:
    s = path.stat()
    user  = pwd.getpwuid(s.st_uid).pw_name
    group = grp.getgrgid(s.st_gid).gr_name
    return user, group


def _systemctl_is_active(service: str) -> bool:
    r = subprocess.run(
        ["systemctl", "is-active", "--quiet", service],
        capture_output=True,
    )
    return r.returncode == 0


def _getfacl(path: str) -> str:
    r = subprocess.run(
        ["getfacl", "-p", path],
        capture_output=True, text=True,
    )
    return r.stdout


def _parse_unit(path: Path) -> dict[str, dict[str, list[str]]]:
    result: dict[str, dict[str, list[str]]] = {}
    section: str | None = None
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
            result.setdefault(section, {})
            continue
        if section and "=" in line:
            key, _, value = line.partition("=")
            result[section].setdefault(key.strip(), []).append(value.strip())
    return result


# ── devagent OS user ──────────────────────────────────────────────────────────

@_skip
class DevagentUserTests(unittest.TestCase):

    def setUp(self):
        self.pw = pwd.getpwnam("devagent")

    def test_user_exists(self):
        self.assertEqual(self.pw.pw_name, "devagent")

    def test_is_system_user(self):
        """System users have uid < 1000."""
        self.assertLess(self.pw.pw_uid, 1000)

    def test_home_is_srv_devagent(self):
        self.assertEqual(self.pw.pw_dir, "/srv/devagent")

    def test_shell_is_nologin(self):
        self.assertIn("nologin", self.pw.pw_shell)


# ── /srv/devagent/ ────────────────────────────────────────────────────────────

@_skip
class SrvDevagentTests(unittest.TestCase):

    def test_dir_owned_by_devagent(self):
        user, group = _owner(_SRV)
        self.assertEqual(user,  "devagent")
        self.assertEqual(group, "devagent")

    def test_env_file_exists(self):
        self.assertTrue((_SRV / ".env").exists())

    def test_env_file_not_world_readable(self):
        """.env must not be readable by others (mode 640 or stricter)."""
        mode = _stat_mode(_SRV / ".env")
        world_read = stat.S_IROTH
        self.assertEqual(mode & world_read, 0, f".env mode is {oct(mode)} — world-readable!")

    def test_env_file_readable_by_owner(self):
        mode = _stat_mode(_SRV / ".env")
        self.assertTrue(mode & stat.S_IRUSR)

    def test_env_file_owner_is_devagent(self):
        user, _ = _owner(_SRV / ".env")
        self.assertEqual(user, "devagent")

    def test_state_dir_exists(self):
        self.assertTrue((_SRV / "state").is_dir())


# ── /srv/devagent/.claude/ (credentials) ─────────────────────────────────────

@_skip
class CredentialsTests(unittest.TestCase):

    _CREDS = _SRV / ".claude" / ".credentials.json"

    def test_claude_dir_exists(self):
        self.assertTrue((_SRV / ".claude").is_dir())

    def test_credentials_file_exists_or_dir_is_restricted(self):
        """Either the credentials exist, or the directory is properly chmod 700
        (which itself prevents us from stat-ing the file as a non-devagent user).
        Both outcomes are acceptable — a PermissionError means the dir is locked down."""
        try:
            exists = self._CREDS.exists()
            self.assertTrue(
                exists,
                "Credentials not copied — run: sudo cp ~/.claude/.credentials.json "
                "/srv/devagent/.claude/ && sudo chown devagent:devagent "
                "/srv/devagent/.claude/.credentials.json && sudo chmod 600 "
                "/srv/devagent/.claude/.credentials.json",
            )
        except PermissionError:
            # Directory is chmod 700 — only devagent can enter.
            # This is the correct security posture; credentials are protected.
            pass

    def test_credentials_not_world_readable(self):
        try:
            if not self._CREDS.exists():
                self.skipTest("credentials file not present")
            mode = _stat_mode(self._CREDS)
            self.assertEqual(mode & stat.S_IROTH, 0, f"credentials mode {oct(mode)} — world-readable!")
            self.assertEqual(mode & stat.S_IWOTH, 0)
        except PermissionError:
            pass  # chmod 700 on parent dir — file is implicitly protected

    def test_credentials_owner_is_devagent(self):
        try:
            if not self._CREDS.exists():
                self.skipTest("credentials file not present")
            user, _ = _owner(self._CREDS)
            self.assertEqual(user, "devagent")
        except PermissionError:
            pass  # chmod 700 on parent dir — only devagent can access it

    def test_claude_dir_not_world_accessible(self):
        mode = _stat_mode(_SRV / ".claude")
        self.assertEqual(mode & (stat.S_IROTH | stat.S_IXOTH), 0)


# ── /usr/local/bin/claude ─────────────────────────────────────────────────────

@_skip
class ClaudeBinaryTests(unittest.TestCase):

    def test_binary_exists(self):
        self.assertTrue(
            _CLAUDE_BIN.exists(),
            "claude not installed at /usr/local/bin/claude — re-run setup script",
        )

    def test_binary_is_executable_by_all(self):
        if not _CLAUDE_BIN.exists():
            self.skipTest("claude binary not present")
        mode = _stat_mode(_CLAUDE_BIN)
        # owner, group, others must all have execute
        required = stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
        self.assertEqual(
            mode & required, required,
            f"claude mode {oct(mode)} — not world-executable",
        )

    def test_binary_is_not_world_writable(self):
        if not _CLAUDE_BIN.exists():
            self.skipTest("claude binary not present")
        mode = _stat_mode(_CLAUDE_BIN)
        self.assertEqual(mode & stat.S_IWOTH, 0)


# ── /var/log/devagent/ ────────────────────────────────────────────────────────

@_skip
class LogDirTests(unittest.TestCase):

    def test_log_dir_exists(self):
        self.assertTrue(_LOG.is_dir())

    def test_log_dir_owned_by_devagent(self):
        user, _ = _owner(_LOG)
        self.assertEqual(user, "devagent")

    def test_log_files_owned_by_devagent(self):
        for log in _LOG.glob("*.log"):
            user, _ = _owner(log)
            self.assertEqual(user, "devagent", f"{log} not owned by devagent")


# ── Deployed service files ────────────────────────────────────────────────────

@_skip
class DeployedServiceFilesTests(unittest.TestCase):

    def setUp(self):
        self.core = _parse_unit(_SYSTEMD_CORE)
        self.ui   = _parse_unit(_SYSTEMD_UI)

    def _get(self, unit, section, key):
        return unit[section][key][0]

    def test_core_user_is_devagent(self):
        self.assertEqual(self._get(self.core, "Service", "User"), "devagent")

    def test_ui_user_is_devagent(self):
        self.assertEqual(self._get(self.ui, "Service", "User"), "devagent")

    def test_core_execstart_matrix_worker(self):
        self.assertIn("core.matrix_worker", self._get(self.core, "Service", "ExecStart"))

    def test_ui_execstart_uvicorn(self):
        self.assertIn("uvicorn", self._get(self.ui, "Service", "ExecStart"))

    def test_core_no_new_privileges(self):
        self.assertEqual(self._get(self.core, "Service", "NoNewPrivileges"), "yes")

    def test_ui_no_new_privileges(self):
        self.assertEqual(self._get(self.ui, "Service", "NoNewPrivileges"), "yes")

    def test_core_protect_system(self):
        self.assertEqual(self._get(self.core, "Service", "ProtectSystem"), "strict")

    def test_ui_protect_system(self):
        self.assertEqual(self._get(self.ui, "Service", "ProtectSystem"), "strict")


# ── Running services ──────────────────────────────────────────────────────────

@_skip
class ServiceStatusTests(unittest.TestCase):

    def test_devagent_service_is_active(self):
        self.assertTrue(
            _systemctl_is_active("devagent"),
            "devagent.service is not active — check: journalctl -u devagent -n 30",
        )

    def test_devagent_ui_service_is_active(self):
        self.assertTrue(
            _systemctl_is_active("devagent-ui"),
            "devagent-ui.service is not active — check: journalctl -u devagent-ui -n 30",
        )


# ── POSIX ACLs on /home/spro/development ─────────────────────────────────────

@_skip
class AclTests(unittest.TestCase):

    _DEV = "/home/spro/development"

    def _acl(self, path: str) -> str:
        return _getfacl(path)

    def test_getfacl_available(self):
        r = subprocess.run(["which", "getfacl"], capture_output=True)
        self.assertEqual(r.returncode, 0, "getfacl not installed")

    def test_devagent_has_rwx_on_development_dir(self):
        acl = self._acl(self._DEV)
        self.assertIn(
            "user:devagent:rwx",
            acl,
            f"devagent does not have rwx on {self._DEV}.\n"
            f"Run: sudo setfacl -R -m u:devagent:rwX {self._DEV}",
        )

    def test_devagent_has_default_acl_on_development_dir(self):
        """Default ACL ensures new files created inside the dir are accessible."""
        acl = self._acl(self._DEV)
        self.assertIn(
            "default:user:devagent:rwx",
            acl,
            f"No default ACL for devagent on {self._DEV}.\n"
            f"Run: sudo setfacl -R -d -m u:devagent:rwX {self._DEV}",
        )

    def test_devagent_can_traverse_home_spro(self):
        acl = self._acl("/home/spro")
        # At minimum execute bit needed to traverse
        self.assertRegex(
            acl,
            r"user:devagent:.*x",
            "devagent cannot traverse /home/spro",
        )

    def test_devagent_cannot_list_home_spro(self):
        """devagent gets traverse-only (x) on /home/spro, not read (r)."""
        acl = self._acl("/home/spro")
        lines = [l for l in acl.splitlines() if "user:devagent:" in l and "default" not in l]
        self.assertTrue(lines, "No devagent ACL entry on /home/spro")
        entry = lines[0]  # e.g. "user:devagent:--x"
        # The r bit (position 1 in rwx) must be '-'
        m = re.search(r"user:devagent:([r-])([w-])([x-])", entry)
        self.assertIsNotNone(m, f"Cannot parse ACL entry: {entry!r}")
        self.assertEqual(m.group(1), "-", f"devagent has read on /home/spro: {entry!r}")


if __name__ == "__main__":
    unittest.main()
