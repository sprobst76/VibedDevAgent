# Contributing to DevAgent

Thanks for your interest in contributing! This document explains how to get
started and what to expect.

---

## Before you start

- Check the [open issues](../../issues) — maybe what you want to build is
  already being worked on or has been discussed.
- For larger changes (new features, architecture), open an issue first to
  discuss the approach before writing code. This saves everyone time.
- All contributions are licensed under [AGPL v3](LICENSE).

---

## Development setup

```bash
git clone https://github.com/sprobst76/VibedDevAgent.git
cd VibedDevAgent

# Web UI dependencies
python3 -m venv .venv
.venv/bin/pip install -r requirements-ui.txt

# Run the tests (no external dependencies required)
python3 -m unittest discover -s tests -p 'test_*.py' -v
```

The Matrix worker has **no external Python dependencies** — standard library only.

---

## How to contribute

1. Fork the repo and create a branch from `main`:
   ```bash
   git checkout -b feat/my-feature
   ```

2. Make your changes. Run the tests:
   ```bash
   python3 -m unittest discover -s tests -p 'test_*.py'
   ```
   All 288+ tests must pass. Add new tests for new behaviour.

3. Commit with a clear message:
   ```
   feat: add scheduled task support (!schedule command)
   fix: prevent duplicate sync on reconnect
   docs: clarify allowed_users format in README
   ```
   Prefixes: `feat`, `fix`, `refactor`, `test`, `docs`, `chore`, `ops`

4. Open a Pull Request against `main`. Describe what the change does and why.

---

## Code style

- **Python 3.10+**, standard library preferred over new dependencies
- Type annotations on all public functions
- Tests use `unittest` (stdlib) — no pytest
- No auto-formatter enforced, but keep consistent with surrounding code
- Comments in English; user-facing messages in the language of the UI
  (currently German, but this is open for discussion)

---

## What we're looking for

Good areas to contribute:

| Area | Examples |
|------|---------|
| **New bot commands** | `!schedule`, `!summary`, `!diff` |
| **Adapter support** | Telegram, Slack, Discord |
| **Web UI** | Project dashboards, job history view |
| **Runner** | PTY mode, better output streaming |
| **Docs** | Setup guides, architecture diagrams |
| **Tests** | Edge cases, integration scenarios |

See [`TODO.md`](TODO.md) for the full backlog.

---

## Pull Request checklist

- [ ] Tests pass (`python3 -m unittest discover -s tests -p 'test_*.py'`)
- [ ] New behaviour has test coverage
- [ ] No credentials, personal paths, or usernames in the code
- [ ] `SECURITY.md` updated if the change affects the security model

---

## Questions?

Open an issue with the `question` label.
