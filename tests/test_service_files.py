"""Tests for ops/systemd service files and setup_devagent_user.sh.

These tests verify the *content* of the files in the repository and do not
require the actual system to be configured.  They run on every CI invocation.
"""

from __future__ import annotations

import re
import stat
import unittest
from pathlib import Path

# ── Paths ─────────────────────────────────────────────────────────────────────

_REPO   = Path(__file__).parent.parent
_SYSDIR = _REPO / "ops" / "systemd"
_SVC    = _SYSDIR / "devagent.service"
_UI_SVC = _SYSDIR / "devagent-ui.service"
_SETUP  = _SYSDIR / "setup_devagent_user.sh"

# ops/systemd/*.service are gitignored (they contain machine-local paths).
# Tests that require them are skipped in CI / on machines without a local checkout.
_HAVE_SVC = _SVC.is_file() and _UI_SVC.is_file()
_skip_no_svc = unittest.skipUnless(
    _HAVE_SVC,
    "ops/systemd/*.service not present (gitignored) — run setup_devagent_user.sh first",
)


# ── Simple unit-file parser ────────────────────────────────────────────────────

def _parse_unit(path: Path) -> dict[str, dict[str, list[str]]]:
    """Return {section: {key: [value, ...]}} for a systemd unit file.

    Comments and blank lines are skipped.  Duplicate keys (e.g. multiple
    ``Environment=`` lines) are collected into a list.
    """
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


def _svc(path: Path) -> dict[str, dict[str, list[str]]]:
    return _parse_unit(path)


def _first(unit: dict, section: str, key: str) -> str:
    return unit[section][key][0]


def _all(unit: dict, section: str, key: str) -> list[str]:
    return unit[section].get(key, [])


# ── devagent.service ──────────────────────────────────────────────────────────

@_skip_no_svc
class CoreServiceTests(unittest.TestCase):

    def setUp(self):
        self.unit = _svc(_SVC)
        self.svc  = self.unit["Service"]

    # identity
    def test_user_is_devagent(self):
        self.assertEqual(_first(self.unit, "Service", "User"), "devagent")

    def test_working_directory_is_vibeddevagent(self):
        wd = _first(self.unit, "Service", "WorkingDirectory")
        self.assertIn("VibedDevAgent", wd)

    def test_environment_file_is_srv_devagent(self):
        self.assertEqual(
            _first(self.unit, "Service", "EnvironmentFile"),
            "/srv/devagent/.env",
        )

    # exec
    def test_execstart_uses_venv_python(self):
        ex = _first(self.unit, "Service", "ExecStart")
        self.assertIn(".venv/bin/python3", ex)

    def test_execstart_runs_matrix_worker(self):
        ex = _first(self.unit, "Service", "ExecStart")
        self.assertIn("core.matrix_worker", ex)

    def test_path_does_not_contain_home_spro(self):
        env_lines = _all(self.unit, "Service", "Environment")
        path_line = next((l for l in env_lines if l.startswith("PATH=")), "")
        self.assertNotIn("/home/spro", path_line)

    # logging
    def test_stdout_goes_to_core_log(self):
        self.assertIn(
            "/var/log/devagent/core.log",
            _first(self.unit, "Service", "StandardOutput"),
        )

    def test_stderr_goes_to_core_log(self):
        self.assertIn(
            "/var/log/devagent/core.log",
            _first(self.unit, "Service", "StandardError"),
        )

    # restart policy
    def test_restart_on_failure(self):
        self.assertEqual(_first(self.unit, "Service", "Restart"), "on-failure")

    # install
    def test_wanted_by_multi_user(self):
        self.assertIn("multi-user.target", _first(self.unit, "Install", "WantedBy"))

    # security hardening
    def test_no_new_privileges(self):
        self.assertEqual(_first(self.unit, "Service", "NoNewPrivileges"), "yes")

    def test_private_tmp(self):
        self.assertEqual(_first(self.unit, "Service", "PrivateTmp"), "yes")

    def test_protect_system_strict(self):
        self.assertEqual(_first(self.unit, "Service", "ProtectSystem"), "strict")

    def test_protect_home_read_only(self):
        self.assertEqual(_first(self.unit, "Service", "ProtectHome"), "read-only")

    def test_restrict_suid_sgid(self):
        self.assertEqual(_first(self.unit, "Service", "RestrictSUIDSGID"), "yes")

    def test_lock_personality(self):
        self.assertEqual(_first(self.unit, "Service", "LockPersonality"), "yes")

    # ReadWritePaths must include required runtime dirs
    def test_rwpaths_includes_srv_devagent(self):
        rwp = _first(self.unit, "Service", "ReadWritePaths")
        self.assertIn("/srv/devagent", rwp)

    def test_rwpaths_includes_log_dir(self):
        rwp = _first(self.unit, "Service", "ReadWritePaths")
        self.assertIn("/var/log/devagent", rwp)

    def test_rwpaths_includes_artifacts(self):
        rwp = _first(self.unit, "Service", "ReadWritePaths")
        self.assertIn("/srv/agent-artifacts", rwp)

    def test_rwpaths_does_not_include_home_spro_development(self):
        """devagent accesses /home/spro/development via POSIX ACL, not ReadWritePaths."""
        rwp = _first(self.unit, "Service", "ReadWritePaths")
        self.assertNotIn("/home/spro/development", rwp)

    # no spro-specific placeholders left over
    def test_no_your_username_placeholder(self):
        raw = _SVC.read_text(encoding="utf-8")
        self.assertNotIn("YOUR_USERNAME", raw)


# ── devagent-ui.service ───────────────────────────────────────────────────────

@_skip_no_svc
class UiServiceTests(unittest.TestCase):

    def setUp(self):
        self.unit = _svc(_UI_SVC)

    def test_user_is_devagent(self):
        self.assertEqual(_first(self.unit, "Service", "User"), "devagent")

    def test_execstart_uses_venv_uvicorn(self):
        ex = _first(self.unit, "Service", "ExecStart")
        self.assertIn(".venv/bin/uvicorn", ex)

    def test_execstart_targets_ui_server(self):
        ex = _first(self.unit, "Service", "ExecStart")
        self.assertIn("ui.server:app", ex)

    def test_execstart_binds_port_20042(self):
        ex = _first(self.unit, "Service", "ExecStart")
        self.assertIn("--port 20042", ex)

    def test_execstart_has_no_app_dir_flag(self):
        """--app-dir is not supported by uvicorn and must not appear."""
        ex = _first(self.unit, "Service", "ExecStart")
        self.assertNotIn("--app-dir", ex)

    def test_development_root_env_set(self):
        env_lines = _all(self.unit, "Service", "Environment")
        dev_root_lines = [l for l in env_lines if "DEVAGENT_DEVELOPMENT_ROOT" in l]
        self.assertTrue(dev_root_lines, "DEVAGENT_DEVELOPMENT_ROOT not set in Environment")
        self.assertIn("/home/spro/development", dev_root_lines[0])

    def test_stdout_goes_to_ui_log(self):
        self.assertIn(
            "/var/log/devagent/ui.log",
            _first(self.unit, "Service", "StandardOutput"),
        )

    def test_after_includes_core_service(self):
        after = _first(self.unit, "Unit", "After")
        self.assertIn("devagent.service", after)

    def test_no_new_privileges(self):
        self.assertEqual(_first(self.unit, "Service", "NoNewPrivileges"), "yes")

    def test_protect_system_strict(self):
        self.assertEqual(_first(self.unit, "Service", "ProtectSystem"), "strict")

    def test_protect_home_read_only(self):
        self.assertEqual(_first(self.unit, "Service", "ProtectHome"), "read-only")

    def test_rwpaths_does_not_include_home_spro_development(self):
        rwp = _first(self.unit, "Service", "ReadWritePaths")
        self.assertNotIn("/home/spro/development", rwp)

    def test_no_your_username_placeholder(self):
        raw = _UI_SVC.read_text(encoding="utf-8")
        self.assertNotIn("YOUR_USERNAME", raw)

    def test_environment_file_is_srv_devagent(self):
        self.assertEqual(
            _first(self.unit, "Service", "EnvironmentFile"),
            "/srv/devagent/.env",
        )

    def test_path_does_not_contain_home_spro(self):
        env_lines = _all(self.unit, "Service", "Environment")
        path_line = next((l for l in env_lines if l.startswith("PATH=")), "")
        self.assertNotIn("/home/spro", path_line)


# ── Symmetry between core and UI service ─────────────────────────────────────

@_skip_no_svc
class ServiceSymmetryTests(unittest.TestCase):
    """Settings that must be identical in both service files."""

    def setUp(self):
        self.core = _svc(_SVC)
        self.ui   = _svc(_UI_SVC)

    def _core(self, key: str) -> str:
        return _first(self.core, "Service", key)

    def _ui(self, key: str) -> str:
        return _first(self.ui, "Service", key)

    def test_same_user(self):
        self.assertEqual(self._core("User"), self._ui("User"))

    def test_same_working_directory(self):
        self.assertEqual(self._core("WorkingDirectory"), self._ui("WorkingDirectory"))

    def test_same_environment_file(self):
        self.assertEqual(self._core("EnvironmentFile"), self._ui("EnvironmentFile"))

    def test_same_no_new_privileges(self):
        self.assertEqual(self._core("NoNewPrivileges"), self._ui("NoNewPrivileges"))

    def test_same_protect_system(self):
        self.assertEqual(self._core("ProtectSystem"), self._ui("ProtectSystem"))

    def test_same_protect_home(self):
        self.assertEqual(self._core("ProtectHome"), self._ui("ProtectHome"))

    def test_same_rwpaths(self):
        self.assertEqual(self._core("ReadWritePaths"), self._ui("ReadWritePaths"))

    def test_same_restart_policy(self):
        self.assertEqual(self._core("Restart"), self._ui("Restart"))


# ── setup_devagent_user.sh ────────────────────────────────────────────────────

class SetupScriptTests(unittest.TestCase):

    def setUp(self):
        self.script = _SETUP.read_text(encoding="utf-8")

    def test_script_file_exists(self):
        self.assertTrue(_SETUP.is_file())

    def test_shebang(self):
        self.assertTrue(self.script.startswith("#!/usr/bin/env bash"))

    def test_strict_mode(self):
        self.assertIn("set -euo pipefail", self.script)

    def test_devagent_user_variable(self):
        self.assertIn('DEVAGENT_USER="devagent"', self.script)

    def test_env_chmod_640(self):
        """Secret .env must be made readable only by the service user."""
        self.assertIn("chmod 640 /srv/devagent/.env", self.script)

    def test_credentials_dst_path(self):
        self.assertIn('CREDS_DST="/srv/devagent/.claude/.credentials.json"', self.script)

    def test_credentials_chmod_600(self):
        """Credentials file must be private to devagent."""
        self.assertIn('chmod 600 "$CREDS_DST"', self.script)

    def test_credentials_dir_chmod_700(self):
        self.assertIn("chmod 700 /srv/devagent/.claude", self.script)

    def test_credentials_chown_devagent(self):
        self.assertIn(
            'chown -R "$DEVAGENT_USER:$DEVAGENT_USER" /srv/devagent/.claude',
            self.script,
        )

    def test_acl_uses_rwx_not_just_rx(self):
        """Write access is required for Claude to edit project files."""
        self.assertIn('u:$DEVAGENT_USER:rwX', self.script)
        # Must NOT fall back to read-only rX for the development directory
        acl_lines = [
            l for l in self.script.splitlines()
            if "setfacl" in l and "/home/spro/development/" in l
        ]
        for line in acl_lines:
            self.assertNotIn(":rX", line, f"ACL line still uses rX: {line!r}")

    def test_acl_sets_default_acl(self):
        """Default ACL ensures new files created in the project are also accessible."""
        self.assertIn("setfacl -R -d -m", self.script)

    def test_acl_traverse_on_home_spro(self):
        """devagent needs x-only on /home/spro to traverse without listing."""
        self.assertIn('setfacl -m "u:$DEVAGENT_USER:x" /home/spro\n', self.script)

    def test_deploys_both_service_files(self):
        self.assertIn("devagent.service", self.script)
        self.assertIn("devagent-ui.service", self.script)

    def test_daemon_reload_after_deploy(self):
        self.assertIn("systemctl daemon-reload", self.script)

    def test_restarts_both_services(self):
        self.assertIn("systemctl restart devagent", self.script)
        self.assertIn("systemctl restart devagent-ui", self.script)

    def test_claude_dst_is_usr_local_bin(self):
        self.assertIn('CLAUDE_DST="/usr/local/bin/claude"', self.script)

    def test_claude_binary_chmod_755(self):
        """claude binary must be world-executable."""
        self.assertIn('chmod 755 "$CLAUDE_DST"', self.script)

    def test_no_your_username_placeholder(self):
        self.assertNotIn("YOUR_USERNAME", self.script)

    def test_log_dir_ownership_fixed(self):
        self.assertIn(
            'chown "$DEVAGENT_USER:$DEVAGENT_USER" /var/log/devagent/',
            self.script,
        )


# ── .example files still exist for documentation ─────────────────────────────

class ExampleFilesTests(unittest.TestCase):

    def test_core_example_exists(self):
        self.assertTrue((_SYSDIR / "devagent.service.example").is_file())

    def test_ui_example_exists(self):
        self.assertTrue((_SYSDIR / "devagent-ui.service.example").is_file())

    def test_actual_files_are_not_example_files(self):
        """The deployed files must differ from the .example templates."""
        core_actual  = _SVC.read_text()
        core_example = (_SYSDIR / "devagent.service.example").read_text()
        self.assertNotEqual(core_actual, core_example)

    def test_example_still_has_placeholder(self):
        """The .example files should retain YOUR_USERNAME so new users know to edit them."""
        example = (_SYSDIR / "devagent.service.example").read_text()
        self.assertIn("YOUR_USERNAME", example)


if __name__ == "__main__":
    unittest.main()
