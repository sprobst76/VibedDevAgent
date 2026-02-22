# Security Policy

## Overview

DevAgent is a personal automation tool designed to run on a private server with
access restricted to trusted operators.  This document describes the threat
model, implemented controls, and known limitations.

---

## Threat Model

| Actor | Trust level |
|-------|-------------|
| Matrix room members in the allow-list | Trusted operators |
| Other Matrix users | Untrusted — commands are ignored |
| Local network / Internet reaching port 20042 | Untrusted — requires UI API key |
| Operating-system processes on the same host | Trusted (no OS-level isolation) |

DevAgent is **not** designed to be exposed to the public Internet without a
reverse proxy and strong authentication.

---

## Implemented Controls

### 1. Input Validation — `core/path_guard.py`

All project names and paths that originate from user input go through strict
validation before being persisted or used in file-system operations.

| Function | What it validates |
|---|---|
| `validate_project_name(name)` | Alphanumeric + `._-`, max 128 chars, no `/\` or shell metacharacters |
| `validate_project_path(path, roots)` | Resolves symlinks and `..`, ensures canonical path is under an allowed root |
| `safe_room_id(room_id)` | Checks `!localpart:server` format against strict regex |

`PathGuardError` (a `ValueError` subclass) is raised on any violation and
surfaced to the caller for user-friendly error messages.

### 2. Web UI Authentication — `ui/server.py`

Set `DEVAGENT_UI_API_KEY` in `/srv/devagent/.env` to enable authentication.
When the variable is empty the UI runs without a password (convenient for
local development; **do not leave empty in production**).

* **Session cookie** `devagent_session` — `HttpOnly`, `SameSite=Lax`,
  1-week TTL. Set `secure=True` in the source when the UI is served over HTTPS.
* **`X-API-Key` header** — for programmatic/curl access.
* `/api/health` is public (monitoring probes).
* All other routes require authentication.
* Constant-time comparison (`hmac.compare_digest`) prevents timing attacks.

### 3. Atomic Token Persistence — `adapters/matrix/client.py`

`_persist_token` writes the new access token to a temporary file in the same
directory as `.env`, then atomically renames it over the target.  This prevents
a partially-written `.env` file on power loss or concurrent writes.

### 4. Tmux Command Quoting — `runner/tmux_driver.py`

The log-file path passed to `tee` in `start_session` is shell-quoted with
`shlex.quote`.  The command itself is wrapped in a subshell `( ... )` to
isolate side-effects from the pipe.

### 5. Matrix Allow-List — `core/matrix_worker.py`

Every incoming Matrix event is matched against `DEVAGENT_ALLOWED_USERS`.
Messages from non-listed senders are silently dropped.

### 6. Sanitized Error Responses

Internal exception details (stack traces, file paths, tokens) are never
returned in HTTP responses.  Errors are logged server-side with full context
and a generic message is sent to the client.

### 7. Systemd Hardening — `ops/systemd/*.service`

Both service units include:

```ini
NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=strict
ReadWritePaths=<explicit list>
ProtectHome=read-only
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectControlGroups=yes
RestrictSUIDSGID=yes
LockPersonality=yes
RestrictRealtime=yes
```

---

## Known Limitations / Out of Scope

* **No encryption at rest** — the `.env` file with the Matrix access token and
  UI API key is stored as plaintext.  Use filesystem permissions (`chmod 600`)
  and rely on the host OS for protection.
* **No HTTPS termination** — the UI listens on plain HTTP.  Deploy behind
  nginx/Caddy with TLS in production.  Remember to flip `secure=True` on the
  session cookie once HTTPS is in place.
* **No CSRF tokens** — POST forms rely on `SameSite=Lax` for cross-site
  request forgery protection, which covers the common browser attack vectors
  but is not a full CSRF-token mitigation.
* **Session cookie equals API key** — the `devagent_session` cookie currently
  stores the raw API key value.  A stolen cookie therefore also enables
  `X-API-Key` header access.  A future improvement is to issue a random
  session token server-side and map it to an expiry timestamp.
* **No 2FA on the Web UI** — only a single API key factor.  TOTP (e.g. via
  `pyotp`) could be added as a second factor if the UI is reachable from
  untrusted networks.
* **Matrix message content** — task instructions arrive via Matrix and are
  passed directly to the Claude CLI.  A malicious operator could craft
  prompts that cause unintended file-system changes.  This is accepted risk
  given the fully-trusted operator model.
* **tmux sessions** — `start_session` accepts an arbitrary shell command
  string assembled by internal code.  No further escaping is applied to
  the command itself; callers must not construct commands from raw user input.
* **No shared-state integrity checks** — the Web UI and the Matrix worker
  communicate via `/srv/devagent/state/projects.json`.  There is no HMAC or
  signature on these files; a compromised Web UI session could alter project
  paths that the worker subsequently acts on.

---

## Deliberately Not Implemented: Matrix E2EE

End-to-end encryption for Matrix messages was evaluated and **consciously
deferred**.  The reasoning is documented here for future reference.

### What Matrix E2EE requires

Matrix E2EE is based on the **Olm/Megolm protocol** — a Double-Ratchet
construction similar to Signal.  Adding it to the bot would require:

1. **`libolm` system dependency** — there is no pure-Python implementation;
   the C library plus Python bindings (`python-olm` or `matrix-nio[e2e]`) are
   mandatory.
2. **Device key management** — every client must register an identity key pair
   (Curve25519 + Ed25519) with the homeserver and continuously replenish
   one-time pre-keys.
3. **Persistent Olm state** — the Olm account and all Megolm session keys must
   survive process restarts (typically stored in an SQLite database).  Loss of
   this state means the bot can no longer decrypt incoming messages.
4. **Room member device tracking** — before sending an encrypted message the
   client must fetch and verify the device list of every room member and
   distribute Megolm session keys to each device via individual Olm channels.
5. **Architecture rewrite** — the only mature Python library with E2EE support
   is `matrix-nio`, which is fully `asyncio`-based.  The current worker uses
   `threading` + `ThreadPoolExecutor`; migrating to asyncio would touch the
   entire sync loop, all locks, and all AI-task scheduling.
6. **Device verification** — without explicit verification (SAS emoji or
   QR-code), E2EE only provides opportunistic encryption (TOFU); a
   compromised homeserver could still inject attacker-controlled devices.

### Why it does not improve security here

The bot process runs on the **same host** as the private Olm key.  An attacker
who gains OS-level access to the server can read the key material directly —
E2EE protects the *transport*, not the *endpoint*.  The effective threat E2EE
would mitigate is a **homeserver operator** (e.g. matrix.org) reading message
content in transit.

For this deployment the accepted mitigations are:

* Use a **self-hosted homeserver** (Synapse / Conduit) so the operator is
  also the server administrator, eliminating the third-party trust issue.
* Or accept that the homeserver sees plaintext, given that message content
  consists of developer task instructions — not credentials or personal data.

### Conditions under which E2EE would be reconsidered

* The bot is migrated to `matrix-nio` + asyncio for unrelated reasons, making
  the library cost zero.
* Message content becomes genuinely sensitive (e.g. secrets passed as task
  arguments) and a third-party homeserver must be used.
* A pure-Python Olm implementation becomes available and stable.

---

## Reporting Vulnerabilities

This project is private.  Report issues directly to the repository owner via
a private channel or by opening a confidential GitHub issue.
