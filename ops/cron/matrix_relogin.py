#!/usr/bin/env python3
"""Proactive Matrix token renewal — intended to run daily via cron or systemd timer.

The DevAgent worker already re-logs in automatically on 401, but this script
provides an additional safeguard by refreshing the token before it expires.

Usage:
    python3 ops/cron/matrix_relogin.py [ENV_FILE]

    ENV_FILE defaults to /srv/devagent/.env

Cron install (example — runs daily at 03:00):
    crontab -e
    0 3 * * * /usr/bin/python3 /home/YOUR_USER/development/VibedDevAgent/ops/cron/matrix_relogin.py /srv/devagent/.env >> /var/log/devagent/relogin.log 2>&1

Systemd timer: see ops/cron/devagent-relogin.timer.example

Required .env variables:
    MATRIX_HOMESERVER_URL     e.g. https://matrix.example.org
    MATRIX_USER_DEVAGENT      e.g. @devagent:example.org
    MATRIX_PASSWORD_DEVAGENT  plaintext password
    MATRIX_ACCESS_TOKEN       updated in-place on success

Exit codes:
    0  success (token updated)
    1  configuration error (missing variables)
    2  login request failed
    3  .env update failed
"""

from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib import error as urllib_error
from urllib import request


# ── .env parser ───────────────────────────────────────────────────────────────

def _parse_env_file(path: Path) -> dict[str, str]:
    """Parse a KEY=VALUE .env file.  Handles comments, blank lines, quoted values."""
    env: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        # Strip inline comments (only after whitespace, to not break tokens containing #)
        value = value.split(" #")[0].split("\t#")[0].strip()
        # Strip matching quotes
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
        if key:
            env[key] = value
    return env


def _update_env_file(path: Path, key: str, new_value: str) -> None:
    """Atomically update a single KEY=value line in *path*."""
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    found = False
    updated: list[str] = []
    for line in lines:
        if line.startswith(f"{key}="):
            updated.append(f"{key}={new_value}\n")
            found = True
        else:
            updated.append(line)
    if not found:
        updated.append(f"{key}={new_value}\n")

    tmp = tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8",
        dir=path.parent, delete=False, suffix=".tmp",
    )
    try:
        tmp.writelines(updated)
        tmp.flush()
        tmp_path = Path(tmp.name)
    finally:
        tmp.close()
    tmp_path.replace(path)


# ── Matrix login ──────────────────────────────────────────────────────────────

def _matrix_login(homeserver_url: str, user: str, password: str) -> str:
    """POST /login and return the new access_token.  Raises on failure."""
    url  = homeserver_url.rstrip("/") + "/_matrix/client/v3/login"
    body = json.dumps({
        "type":     "m.login.password",
        "user":     user,
        "password": password,
    }).encode("utf-8")
    req = request.Request(
        url, method="POST", data=body,
        headers={"Content-Type": "application/json", "User-Agent": "devagent-relogin/1.0"},
    )
    try:
        with request.urlopen(req, timeout=30) as resp:  # noqa: S310
            result = json.loads(resp.read().decode("utf-8"))
        token = result.get("access_token", "")
        if not token:
            raise RuntimeError(f"login response missing access_token: {result}")
        return token
    except urllib_error.HTTPError as exc:
        body_text = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
        raise RuntimeError(f"HTTP {exc.code}: {body_text[:200]}") from exc


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> int:
    env_file = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("/srv/devagent/.env")
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    if not env_file.exists():
        print(f"[{ts}] ERROR: env file not found: {env_file}", flush=True)
        return 1

    env = _parse_env_file(env_file)

    homeserver = env.get("MATRIX_HOMESERVER_URL", "").strip()
    user       = env.get("MATRIX_USER_DEVAGENT", "").strip()
    password   = env.get("MATRIX_PASSWORD_DEVAGENT", "").strip()

    missing = [k for k, v in [
        ("MATRIX_HOMESERVER_URL",    homeserver),
        ("MATRIX_USER_DEVAGENT",     user),
        ("MATRIX_PASSWORD_DEVAGENT", password),
    ] if not v]

    if missing:
        print(f"[{ts}] ERROR: missing variables in {env_file}: {', '.join(missing)}", flush=True)
        return 1

    print(f"[{ts}] Logging in as {user} @ {homeserver} …", flush=True)

    try:
        new_token = _matrix_login(homeserver, user, password)
    except Exception as exc:
        print(f"[{ts}] ERROR: login failed: {exc}", flush=True)
        return 2

    try:
        _update_env_file(env_file, "MATRIX_ACCESS_TOKEN", new_token)
    except Exception as exc:
        print(f"[{ts}] ERROR: could not update {env_file}: {exc}", flush=True)
        return 3

    print(f"[{ts}] OK: token updated ({new_token[:16]}…)", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
