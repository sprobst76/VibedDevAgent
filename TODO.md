# TODO -- DevAgent MVP Roadmap (P0/P1/P2)

Statusdatum: 2026-02-20
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

### 14) Option C: devagent-User vollständig einrichten ⚠️ HOHE PRIO
**Hintergrund:** Aktuell läuft der Service als der Operator-User (temporäre Lösung, Option A).
Das ist für Entwicklung ok, aber nicht für produktiven Betrieb.

- [ ] `devagent`-User mit Login-Shell ausstatten: `sudo usermod -s /bin/bash devagent`
- [ ] Home-Dir für devagent einrichten (z.B. `/srv/devagent-home`) für `~/.claude/` Config
- [ ] Node.js via nvm für `devagent` installieren
- [ ] `claude` CLI für `devagent` installieren: `npm install -g @anthropic-ai/claude-code`
- [ ] Claude-Auth für devagent einrichten: entweder OAuth (`claude auth login`) oder API Key in `.env`
- [ ] Service-Unit zurück auf `User=devagent` stellen
- [ ] Log-Verzeichnis `/var/log/devagent/` Ownership auf `devagent` korrigieren
- [ ] Testen: `sudo -u devagent claude --version`

**Warum wichtig:** Solange der Service als Operator-User läuft, hat ein bösartiger Job-Command
Zugriff auf SSH-Keys, Browser-Profile und alle anderen Projekte des Users.

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
- [ ] Bei 401-Fehler im Worker: automatisch `matrix_login.sh` aufrufen oder Refresh-Token-Flow implementieren
- [ ] Alternativ: Cronjob der `matrix_login.sh operator` täglich ausführt
- [ ] Ziel: kein manueller Token-Wechsel mehr nötig

### 17) Lange Antworten aufteilen
- [ ] Claude-Ausgaben > 3800 Zeichen in mehrere Matrix-Nachrichten splitten (sinnvolle Trennung nach Absätzen)
- [ ] Alternativ: als Matrix-File-Upload (m.file event)
- [ ] MAX_OUTPUT_CHARS in .env konfigurierbar machen

### 18) Live-Log im Browser
- [x] Server-Sent Events Endpoint `/api/logs/stream`
- [x] Letzte 100 Zeilen von `core.log` + live tail
- [x] UI-Panel in der Detail-Ansicht

### 19) Multi-Backend UI
- [x] UI kann mehrere Backend-URLs konfigurieren (z.B. Heim-PC + VPS)
- [x] Jeder Backend liefert `/api/health` + `/api/projects` + `/api/jobs`
- [x] Aggregierte Ansicht aller Backends in einem Frontend

### 21) Scheduled Tasks
- [ ] `!schedule "täglich 09:00" <aufgabe>` — Cron-ähnliche Tasks pro Raum
- [ ] Gespeichert in SQLite oder JSON
- [ ] Inspiriert von NanoClaw task-scheduler.ts

### 22) PTY-Modus für Claude Code
- [ ] `subprocess.Popen` mit `pty.openpty()` für bessere Kompatibilität
- [ ] Vor allem relevant wenn `--print` entfernt wird
- [ ] Referenz: OpenClaw coding-agent SKILL.md `pty:true`

### 23) Projekt-TODO-Übersicht in Matrix und Web UI
- [x] `todo_parser.py` erweitern: scannt `<local_path>/TODO.md` pro Projekt aus der Registry
- [x] Matrix: `!todo @<projektname>` zeigt offene TODOs des jeweiligen Projekts
- [x] Matrix: `!todo` ohne Argument zeigt Zusammenfassung aller Projekte (N offen je Projekt)
- [x] Web UI: Projekt-Detail-Panel (`partials/project_detail.html`) zeigt offene TODO-Items
- [x] Web UI: `/todos`-Seite bekommt Tab-Wechsel "DevAgent" / "Projekte"
- [x] Fallback: kein TODO.md im Projekt → Hinweis statt Fehler

## P4 — Zurückgestellt / Nice-to-have

### 20) Kein `!ai`-Prefix in Projekt-Räumen (opt-in)
- [ ] Per CLAUDE.md oder projects.json: `auto_reply: true` für einen Raum
- [ ] Jede Nachricht von erlaubten Usern wird direkt an Claude weitergeleitet
- [ ] Mention-Gating als Alternative (nur bei @devagent-bot)
