# DevAgent

Matrix-first Dev Orchestrator mit Worktrees, tmux-Runner und Approval-Gates.

## Aktueller Stand
- Projekt-Spezifikation: `DevAgent_Project_Specification.md`
- Umsetzungsplan: `TODO.md`
- Erste MVP-Skeletons fuer P0 angelegt

## Struktur
- `core/` Agent Core und State-Machine
- `adapters/` Matrix/Telegram Adapter
- `runner/` tmux Driver und Review-Pipeline
- `scripts/` lokale Betriebs-Skripte (Worktree)
- `ops/` Deployment und Service-Definitionen
- `docs/` Architektur, Security, Zuverlaessigkeit
- `tests/` E2E- und Abnahmetests

## Schnellstart (Scaffold)
```bash
ls -la
sed -n '1,200p' TODO.md
```

## Lokaler Flow-Test
```bash
python3 -m core.main \
  --allowed-users '@alice:example.org,@bob:example.org' \
  --user-id '@alice:example.org' \
  --reaction '✅' \
  --artifacts-root /tmp/devagent-artifacts
```

## Tests
```bash
python3 -m unittest discover -s tests -p 'test_*.py'
```

## MVP Smoke
```bash
scripts/mvp_smoke.sh
```

## Live-Test auf Zielhost
```bash
scripts/live_test.sh
```

## Service installieren
```bash
sudo PROJECT_DIR=/srv/devagent bash /srv/devagent/ops/systemd/install_service.sh
```

## Matrix Live Worker (lokal)
Der systemd-Service startet den Worker als:
`python3 -m core.matrix_worker`

Wichtige `.env` Werte:
- `MATRIX_HOMESERVER_URL`
- `MATRIX_ACCESS_TOKEN`
- `MATRIX_ROOM_ID`
- `DEVAGENT_ALLOWED_USERS`

Job in Matrix anlegen (CLI-Helfer):
```bash
python3 scripts/matrix_send_jobcard.py \
  --job-id 1001 \
  --repo devagent-live-test \
  --branch main \
  --command "echo matrix-job && sleep 2" \
  --requested-by "@youruser:matrix.org"
```

Hinweis:
- Standard `--mode text` sendet eine sichtbare Nachricht in Element.
- Auf diese Nachricht kannst du mit `✅` / `🛑` reagieren.

Praktische Matrix-Checks:
```bash
# Raum-Metadaten testen
python3 scripts/matrix_room_info.py

# Einzelnes Event holen
python3 scripts/matrix_get_event.py --event-id '$EVENT_ID'

# Alles in einem Ablauf
bash scripts/matrix_quick_test.sh

# Voller No-Sudo-Autotest (isoliert unter /tmp, inkl. approve/stop)
bash scripts/matrix_no_sudo_test.sh

# Live-Events im Terminal ansehen (ohne Element)
python3 scripts/matrix_tail.py --once
python3 scripts/matrix_tail.py
```

## Post-Reboot Check
```bash
bash ops/checks/post_reboot_check.sh
```
