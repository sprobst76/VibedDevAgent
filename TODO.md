# TODO -- DevAgent MVP Roadmap (P0/P1/P2)

Statusdatum: 2026-02-26
Quelle: `DevAgent_Project_Specification.md`

## P0 -- MVP zwingend (erst fertigstellen)

### 1) Projektgeruest und Basisstruktur
- [x] Verzeichnisstruktur anlegen (`core`, `adapters`, `runner`, `ops`, `docs`, `tests`)
- [x] Grunddateien anlegen (`README.md`, `.env.example`, `.gitignore`)
- [x] TODO und Spezifikation im Repo belassen

```bash
mkdir -p core adapters runner ops docs tests
touch README.md .env.example .gitignore
```

### 2) Job- und State-Model definieren
- [x] Job-States exakt wie Spezifikation implementierbar dokumentieren
- [x] Event-Typen definieren (`approve`, `reject`, `stop`)
- [x] Guards je State festlegen (welche Aktion wann erlaubt ist)

```bash
mkdir -p docs
cat > docs/state-machine.md <<'EOF'
RECEIVED -> PLANNING -> WAIT_APPROVAL -> RUNNING -> RUN_TESTS -> REVIEWING -> DONE|FAILED|CANCELLED
Events: approve, reject, stop
EOF
```

### 3) Worktree-Manager (pro Job isoliert)
- [x] `create`: Worktree fuer Job erstellen
- [x] `diff`: Diff gegen Basis-Branch erzeugen
- [x] `cleanup`: Worktree sicher entfernen
- [x] Artefaktordner pro Job erzeugen (`/srv/agent-artifacts/job-xxxxx`)

```bash
mkdir -p scripts
cat > scripts/worktree_create.sh <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
REPO="$1"; JOB_ID="$2"; BASE_BRANCH="${3:-main}"
WT="/srv/agent-worktrees/${REPO}/job-${JOB_ID}"
mkdir -p "$(dirname "$WT")"
git -C "/srv/repos/${REPO}" worktree add "$WT" "$BASE_BRANCH"
mkdir -p "/srv/agent-artifacts/job-${JOB_ID}"
echo "$WT"
EOF
chmod +x scripts/worktree_create.sh
```

### 4) Matrix Adapter (MVP)
- [x] Matrix-Room Listener empfaengt Job-Anfragen
- [x] JobCard Event erzeugen
- [x] Reactions mappen: `✅=approve`, `❌=reject`, `🛑=stop`
- [x] User-Allowlist pruefen
- [x] Nur gueltige Actions im passenden State zulassen

```bash
mkdir -p adapters/matrix
touch adapters/matrix/README.md
```

### 5) Runner + tmux Driver
- [x] tmux-Session pro Job starten
- [x] Kommandos in Session ausfuehren
- [x] Logs in Artefakte schreiben
- [x] Stop-Event beendet Session robust

```bash
mkdir -p runner
touch runner/README.md
```

### 6) Approval-Gate und Sicherheitsmodus
- [x] Default: read-only/planning bis explizite Approval
- [x] Writes/Exec nur nach Approval erlauben
- [x] Audit-Log pro genehmigter Aktion

```bash
mkdir -p docs
touch docs/security-model.md
```

### 7) Service-Betrieb
- [x] Systemd-Service fuer Core definieren
- [x] Restart-Policy und Logging konfigurieren
- [ ] Start nach Reboot pruefen

```bash
mkdir -p ops/systemd
touch ops/systemd/devagent.service
```

### 8) MVP-Abnahmetest (DoD)
- [x] Matrix-Job erstellt Worktree
- [x] Approval startet Runner
- [x] Logs + Diff abrufbar
- [x] Stop bricht Job ab
- [x] Cleanup entfernt Worktree

```bash
mkdir -p tests/e2e
touch tests/e2e/mvp_acceptance.md
```

## P1 -- kurz nach MVP

### 9) Telegram Control Channel
- [x] Bot-Commands: `approve`, `stop`, `status`
- [x] Gleiche Rechte-/State-Logik wie Matrix erzwingen
- [x] Token sicher verwalten (nur env/secrets)

```bash
mkdir -p adapters/telegram
touch adapters/telegram/README.md
```

### 10) Review Pipeline ausbauen
- [x] Standardisierte Review-Zusammenfassung (A-E Format)
- [x] Test-Hooks je Repository konfigurierbar
- [x] Fehlerklassifikation (infra/tool/test/code)

```bash
mkdir -p runner/review
touch runner/review/README.md
```

### 11) Robustheit
- [x] Retry + Timeout fuer Adapter
- [x] Idempotenz bei approve/stop
- [x] Crash-safe Cleanup beim Neustart

```bash
mkdir -p docs
touch docs/reliability.md
```

## P1-SECURITY -- Service-User Härtung (HOHE PRIORITÄT)

### 14) Option C: devagent-User vollständig einrichten ✓
**Umgesetzt Feb 2026** — Ansatz: ELF-Binary kopieren + POSIX-ACLs statt nvm/npm.

- [x] Home-Dir `/srv/devagent/` + Ownership `devagent:devagent`
- [x] `claude` CLI nach `/usr/local/bin/claude` kopiert (chmod 755)
- [x] OAuth-Credentials nach `/srv/devagent/.claude/.credentials.json` (chmod 600)
- [x] POSIX-ACL: `devagent` hat `rwX` auf `/home/spro/development/` (rekursiv + default)
- [x] Service-Units auf `User=devagent` umgestellt
- [x] Log-Verzeichnis `/var/log/devagent/` Ownership korrigiert
- [x] Migration-Skript: `ops/systemd/setup_devagent_user.sh`
- [x] 103 Tests: `test_service_files.py` + `test_system_deployment.py`

**Hinweis:** Nach `claude update` das Setup-Skript erneut ausführen (Binary + Credentials sync).

### 15) Option E: Direkte Anthropic API (mittelfristige Alternative)
**Hintergrund:** Statt `claude` CLI direkt die Python-API nutzen.

- [ ] `anthropic` Python-Package als einzige externe Abhängigkeit erlauben
- [ ] Eigenen Agentic-Loop implementieren (Tool-Calls: read_file, write_file, run_shell)
- [ ] Prompt-Templates pro Task-Typ definieren
- [ ] Kosten-Tracking (Token-Usage pro Job in audit.jsonl)
- [ ] Vorteil: Kein Node.js, kein Home-Dir-Problem, volle Kontrolle über Prompts

**Wann sinnvoll:** Wenn claude CLI zu viel Overhead hat oder feinere Steuerung
(Kosten, Kontext, Tools) gewünscht wird.

## P2 -- Ausbau

### 12) Web-UI auf VPS (on-demand)
- [x] Read-only Dashboard (Jobs, Status, Logs, Diff)
- [x] Zugriff nur via Tailscale
- [x] Kein permanenter Pull vom Heim-PC

```bash
mkdir -p ui
touch ui/README.md
```

### 13) Optional Event Push
- [x] Push-Benachrichtigungen fuer Statuswechsel
- [x] Nutzbare Filter (nur failed/done/approval-needed)

```bash
mkdir -p docs
touch docs/event-push.md
```

## Empfohlene Reihenfolge (konkret)
1. P0.1-P0.3 (Basis + State + Worktree)
2. P0.5 (Runner/tmux)
3. P0.4 (Matrix Adapter + Reactions)
4. P0.6-P0.7 (Approval/Security + Service)
5. P0.8 (MVP-Abnahme)
6. P1 danach P2

---

## P3 — AI Agent Verbesserungen (priorisiert 2026-02)

### 16) Token Auto-Refresh
- [x] Startup-Warning wenn `MATRIX_USER_DEVAGENT`/`MATRIX_PASSWORD_DEVAGENT` fehlen
- [x] Auto-Relogin bei 401 bereits in `adapters/matrix/client.py` implementiert
- [x] Cronjob als zusätzliche Absicherung: `ops/cron/matrix_relogin.py` (täglich 03:00)
- [x] Systemd-Timer: `ops/cron/devagent-relogin.{service,timer}.example`
- [x] 19 Tests in `tests/test_matrix_relogin.py`

### 17) Lange Antworten aufteilen
- [x] Splitting bei `\n\n`-Grenzen implementiert (`_split_for_matrix`, max 3800 Zeichen)
- [x] Smart hard-cut: bevorzugt Zeilen- dann Wortgrenzen
- [x] `MAX_OUTPUT_CHARS` in `.env` konfigurierbar machen (`DEVAGENT_MAX_OUTPUT_CHARS`, default 65536)

### 18) Live-Log im Browser
- [x] Server-Sent Events Endpoint `/api/logs/stream`
- [x] Letzte 100 Zeilen von `core.log` + live tail
- [x] UI-Panel in der Detail-Ansicht

### 19) Multi-Backend UI
- [x] UI kann mehrere Backend-URLs konfigurieren (z.B. Heim-PC + VPS)
- [x] Jeder Backend liefert `/api/health` + `/api/projects` + `/api/jobs`
- [x] Aggregierte Ansicht aller Backends in einem Frontend

### 21) Scheduled Tasks ✓
- [x] `!schedule "täglich 09:00" <aufgabe>` — Cron-ähnliche Tasks pro Raum
- [x] Gespeichert in JSON (`DEVAGENT_SCHEDULES_FILE`), atomic writes
- [x] `!schedules` — Liste aller Schedules im Raum
- [x] `!unschedule <id>` — Schedule entfernen
- [x] Ausdrücke: `täglich HH:MM`, `stündlich`, `montags HH:MM`, `0 9 * * *`
- [x] Daemon-Thread (`ScheduledTaskRunner`), prüft alle 30s, no double-fire
- [x] 33 Tests in `tests/test_scheduler.py`

### 22) PTY-Modus für Claude Code ✓
- [x] `subprocess.Popen` mit `pty.openpty()` für bessere Kompatibilität
- [x] ANSI-Escape-Sequenzen werden automatisch gefiltert (`_strip_ansi`)
- [x] `\r\n`-Normalisierung für PTY-Zeilenenden
- [x] `DEVAGENT_USE_PTY=1` env-Flag (default: 0 = Pipe-Modus)
- [x] Fallback auf Pipe-Modus wenn `pty`-Modul nicht verfügbar
- [x] 12 neue Tests in `tests/test_ai_handler.py` (397 gesamt)

### 23) Projekt-TODO-Übersicht in Matrix und Web UI
- [x] `todo_parser.py` erweitern: scannt `<local_path>/TODO.md` pro Projekt aus der Registry
- [x] Matrix: `!todo @<projektname>` zeigt offene TODOs des jeweiligen Projekts
- [x] Matrix: `!todo` ohne Argument zeigt Zusammenfassung aller Projekte (N offen je Projekt)
- [x] Web UI: Projekt-Detail-Panel (`partials/project_detail.html`) zeigt offene TODO-Items
- [x] Web UI: `/todos`-Seite bekommt Tab-Wechsel "DevAgent" / "Projekte"
- [x] Fallback: kein TODO.md im Projekt → Hinweis statt Fehler

### 24) Claude Code Permissions aufräumen ✓
- [x] `.claude/settings.json` anlegen (committed, Agent/LLM-Template)
- [x] `rm` auf Projektpfad + `/tmp/VibedDevAgent*` eingeschränkt
- [x] `deny`-Regeln: `ssh*`, `rm -rf /home*`, `/srv*`, `/root*`, `shutdown*`
- [x] `settings.local.json` bereinigt (war ~60 Zeilen Müll → 4 sudo-Einträge)
- [x] `.claude/settings.local.json` in `.gitignore` eingetragen
- [x] Explizite `git add*`/`commit*`/`push*`-Einträge in `settings.json` (kein Permission-Prompt)

### 25) Proaktive TODO-Vorschläge nach Job-Abschluss ✓
- [x] `next_open_todo(sections)` in `todo_parser.py` — erstes offenes Item nach Priorität
- [x] `_suggest_next_todo(room_id)` in `matrix_worker.py` — nach erfolgreichem Job
- [x] Opt-in via `DEVAGENT_PROACTIVE_TODOS=1` (default: 0)
- [x] Nur wenn Raum ein Projekt mit `TODO.md` hat und offene Items existieren
- [x] 12 neue Tests (6 `NextOpenTodoTests` + 6 `SuggestNextTodoTests`, 428 gesamt)

### 27) SVG Favicon ✓
- [x] `>_` Terminal-Prompt im Dracula-Look (cyan Chevron + lila Underscore)
- [x] Direkt in `server.py` als Inline-SVG, keine Static-Files nötig
- [x] `/favicon.ico` zu `_PUBLIC_PATHS` hinzugefügt (kein Auth erforderlich)
- [x] `<link rel="icon">` in `base.html`

### 26) Watchdog WAIT_APPROVAL Timeout ✓
- [x] `JobRecord.wait_approval_at` — Timestamp wenn Job in WAIT_APPROVAL geht
- [x] `DevAgentEngine.waiting_jobs()` — gibt alle WAIT_APPROVAL-Jobs zurück
- [x] `JobWatchdog._check_waiting_job()` — auto-FAILED nach Timeout
- [x] `DEVAGENT_MAX_WAIT_APPROVAL_SECONDS=3600` (default 1h, konfigurierbar)
- [x] 10 neue Tests in `test_watchdog.py` (541 gesamt)

### 31) Job History Persistence ✓
- [x] `DevAgentEngine.load_from_artifacts()` — liest `audit.jsonl` und restauriert `started_at` / `wait_approval_at`
- [x] Nur aktive Jobs (RUNNING/WAIT_APPROVAL) werden gescannt (performant)
- [x] Bereits gesetzte Timestamps werden nicht überschrieben (idempotent)
- [x] Aufruf in `MatrixWorker._restore_engine_jobs()` nach State-File-Restore
- [x] 6 neue Tests in `test_engine_audit.py` (547 gesamt)

### 32) HTMX Offline Handling ✓
- [x] Roter Offline-Banner (fixed, oben) — nur sichtbar wenn Verbindung weg
- [x] JS zählt `htmx:sendError` + `htmx:responseError (5xx)` — Banner nach 3 Fehlern
- [x] Polling-Elemente (`hx-trigger="every …"`) werden pausiert (`da-reconnect`-Trigger)
- [x] Health-Check via `fetch('/api/health')` alle 10s wenn offline
- [x] Bei Wiederherstellung: Banner weg, Polling-Trigger zurücksetzen, sofort-Poll
- [x] Native `window.online/offline`-Events als schnelles zusätzliches Signal

### 33) GitHub Actions CI Monitor ✓
- [x] `adapters/github/client.py` — `detect_github_repo()` (git remote auto-detect), `fetch_workflow_runs()`, `latest_per_workflow()`, `run_conclusion()`, `overall_conclusion()`
- [x] `core/ci_monitor.py` — `CIMonitor` Daemon-Thread (analog JobWatchdog), `fetch_status_for_projects()`, `format_ghstatus()`
- [x] Matrix: `!ghstatus` — alle Projekte; `!ghstatus @ProjektName` — einzeln
- [x] Background-Polling alle `DEVAGENT_CI_CHECK_INTERVAL=300`s, Notify nur bei Status-Änderung (fail↔ok)
- [x] `GITHUB_TOKEN` env var; Monitor deaktiviert wenn leer
- [x] Proaktiver Hinweis bei failure: `→ !ai @Projekt Analysiere den fehlgeschlagenen GitHub Build …`
- [x] 22 neue Tests in `tests/test_github_client.py` (569 gesamt)

## P4 — Zurückgestellt / Nice-to-have

### 20) Kein `!ai`-Prefix in Projekt-Räumen (opt-in)
- [ ] Per CLAUDE.md oder projects.json: `auto_reply: true` für einen Raum
- [ ] Jede Nachricht von erlaubten Usern wird direkt an Claude weitergeleitet
- [ ] Mention-Gating als Alternative (nur bei @devagent-bot)
