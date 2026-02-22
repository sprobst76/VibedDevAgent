# DevAgent

A Matrix-first developer automation tool. Send tasks to a bot via Matrix chat,
approve them with a reaction, and let Claude Code execute them in an isolated
worktree — with a live web UI to manage projects and monitor logs.

```
You (Matrix) ──✅──▶ DevAgent Bot ──▶ Claude Code ──▶ Git Worktree
                          │
                    Web UI :20042
```

## Features

- **Matrix bot** — send `!ai <task>` in any project room; approve with ✅, cancel with 🛑
- **Multi-room** — one worker handles all registered project rooms simultaneously
- **Claude Code integration** — runs `claude --print` in an isolated worktree per job
- **Long responses** — answers > 3 800 chars are split into labelled parts automatically
- **Web UI** — project registry, room linking, CLAUDE.md editor, live log stream
- **Token auto-refresh** — re-logs in automatically on 401, updates `.env` atomically
- **Systemd services** — hardened units for both worker and UI

---

## Requirements

- Python 3.10+
- [Claude Code CLI](https://github.com/anthropics/claude-code) (`claude` on PATH)
- tmux
- git
- A Matrix account + access token ([how to get one](#get-a-matrix-access-token))

---

## Setup

### 1. Clone and install dependencies

```bash
git clone https://github.com/sprobst76/VibedDevAgent.git
cd VibedDevAgent

# Web UI dependencies (FastAPI + Jinja2)
python3 -m venv .venv
.venv/bin/pip install -r requirements-ui.txt
```

The Matrix worker itself has **no external Python dependencies** — it uses only
the standard library.

### 2. Create `/srv/devagent` and configure environment

```bash
sudo mkdir -p /srv/devagent/{state,logs}
sudo chown -R $USER /srv/devagent

cp .env.example /srv/devagent/.env
chmod 600 /srv/devagent/.env
```

Edit `/srv/devagent/.env` and fill in at minimum:

```env
MATRIX_HOMESERVER_URL=https://matrix.org      # or your homeserver
MATRIX_ACCESS_TOKEN=<your token>              # see below
MATRIX_ROOM_ID=!yourroom:matrix.org           # primary control room
DEVAGENT_ALLOWED_USERS=@you:matrix.org        # comma-separated allow-list
DEVAGENT_UI_API_KEY=<random string>           # protects the web UI
```

### 3. Get a Matrix access token

```bash
curl -s -X POST "https://matrix.org/_matrix/client/v3/login" \
  -H "Content-Type: application/json" \
  -d '{"type":"m.login.password","user":"@you:matrix.org","password":"yourpassword"}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])"
```

Paste the result into `MATRIX_ACCESS_TOKEN` in your `.env`.

For automatic token refresh on expiry, also set:
```env
MATRIX_USER_DEVAGENT=@you:matrix.org
MATRIX_PASSWORD_DEVAGENT=yourpassword
```

### 4. Create required directories

```bash
sudo mkdir -p /srv/repos /srv/agent-worktrees /srv/agent-artifacts
sudo mkdir -p /var/log/devagent
sudo chown -R $USER /srv/repos /srv/agent-worktrees /srv/agent-artifacts /var/log/devagent
```

### 5. Install and start systemd services

```bash
# Edit the service files first — replace YOUR_USERNAME with your OS username
nano ops/systemd/devagent.service
nano ops/systemd/devagent-ui.service

sudo cp ops/systemd/devagent.service     /etc/systemd/system/
sudo cp ops/systemd/devagent-ui.service  /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now devagent devagent-ui
```

Check status:

```bash
sudo systemctl status devagent devagent-ui
journalctl -u devagent -f
```

### 6. Open the Web UI

Navigate to `http://your-server:20042` and log in with the `DEVAGENT_UI_API_KEY`
you set in `.env`.

From the UI you can:
- Register projects and link them to Matrix rooms
- Create new Matrix rooms directly
- Edit `CLAUDE.md` per project
- Watch the live log stream

---

## Usage

### Send a task

In any registered Matrix room, send:

```
!ai refactor the login module to use dataclasses
```

The bot responds with a confirmation. React with **✅** to run it or **🛑** to cancel.

### Other commands

| Command | Description |
|---------|-------------|
| `!ai <task>` | Run a Claude Code task in this room's project |
| `!status` | Show worker status and active rooms |
| `!cancel` | Cancel the currently running task in this room |

### Project rooms

Each project can be linked to a dedicated Matrix room. The bot automatically
routes `!ai` commands to the correct project directory based on the room.

---

## Running locally (without systemd)

```bash
# Matrix worker
cd /srv/devagent
python3 -m core.matrix_worker

# Web UI (separate terminal)
.venv/bin/uvicorn ui.server:app --host 127.0.0.1 --port 20042 \
  --app-dir /path/to/VibedDevAgent
```

---

## Tests

```bash
python3 -m unittest discover -s tests -p 'test_*.py' -v
```

288 tests, no external dependencies required.

---

## Project layout

```
core/               Worker, state machine, path validation
adapters/matrix/    Matrix HTTP client, AI handler
runner/             tmux driver, job runner, worktree manager
ui/                 FastAPI web UI, project registry, templates
ops/systemd/        Service unit files
scripts/            Operational scripts (login, deploy, diagnostics)
tests/              Unit tests (stdlib unittest only)
SECURITY.md         Threat model, implemented controls, known limitations
```

---

## Security

See [SECURITY.md](SECURITY.md) for the full threat model and list of
implemented controls. Key points:

- Web UI is protected by an API key (`DEVAGENT_UI_API_KEY`)
- Matrix commands are filtered by an allow-list (`DEVAGENT_ALLOWED_USERS`)
- All project names and paths are validated against traversal and injection
- Systemd units include `NoNewPrivileges`, `PrivateTmp`, `ProtectSystem` and
  related hardening directives
- The UI should be placed behind a TLS-terminating reverse proxy (nginx/Caddy)
  before exposing it beyond localhost

---

## Post-reboot check

```bash
bash ops/checks/post_reboot_check.sh
```

## License

[GNU AGPL v3](LICENSE) — free to use, modify, and distribute, including
commercially. If you run a modified version as a network service, you must
make the source available. Contributions welcome — see [CONTRIBUTING.md](CONTRIBUTING.md).

---

## Diagnostic scripts

```bash
# Watch live Matrix events in the terminal
python3 scripts/matrix_tail.py

# Check room metadata
python3 scripts/matrix_room_info.py

# Full no-sudo integration test (runs under /tmp)
bash scripts/matrix_no_sudo_test.sh
```
