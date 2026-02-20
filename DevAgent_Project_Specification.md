# DevAgent -- Matrix-first Dev Orchestrator (Worktree + tmux)

Claude Code + Codex über tmux · Matrix/Element Workspace (Reactions) ·
Telegram Control · Web-Dashboard auf VPS\
Start der Entwicklung: Heimserver (über Tailscale erreichbar)

Generated on: 2026-02-20 12:22:19

------------------------------------------------------------------------

## 1. Vision

Ein sicherer Dev-only Agent mit:

-   Plan → Approval → Execute → Review
-   Worktree pro Job
-   Matrix als Haupt-Workspace
-   Telegram als mobiler Control-Kanal
-   Web UI auf VPS ohne permanenten Pull vom Heim-PC
-   Read-only default + Approval für Writes/Exec

------------------------------------------------------------------------

## 2. Architektur

-   Agent Core (Heimserver)
-   Matrix Adapter (Rooms + Reactions)
-   Telegram Adapter (Approve/Stop/Status)
-   Web UI (VPS, on-demand Zugriff via Tailscale)
-   tmux Driver (Codex / Claude / Runner)
-   Worktree Manager (pro Job isoliert)

------------------------------------------------------------------------

## 3. Worktree Konzept

Main Checkout (clean): /srv/repos/`<repo>`{=html}

Worktrees: /srv/agent-worktrees/`<repo>`{=html}/job-00017/

Artifacts: /srv/agent-artifacts/job-00017/

Flow: 1) git worktree add 2) Arbeiten im Worktree 3) git diff / Preview
4) Approval 5) Cleanup

------------------------------------------------------------------------

## 4. State Machine

RECEIVED\
→ PLANNING\
→ WAIT_APPROVAL\
→ RUNNING\
→ RUN_TESTS\
→ REVIEWING\
→ DONE \| FAILED \| CANCELLED

------------------------------------------------------------------------

## 5. Matrix Reactions

JobCard Event reagiert auf:

✅ approve\
❌ reject\
🛑 stop

Nur gültig im richtigen State und von erlaubten Usern.

------------------------------------------------------------------------

## 6. Implementierungsphasen

Phase 1 -- Core + Worktree + tmux\
Phase 2 -- Matrix Adapter\
Phase 3 -- Runner + Review Pipeline\
Phase 4 -- Telegram Adapter\
Phase 5 -- Web UI (VPS, on-demand)\
Phase 6 -- Hardening & Optional Event Push

------------------------------------------------------------------------

## 7. Definition of Done (MVP)

-   Job per Matrix erzeugt Worktree
-   Approval via Reaction startet Ausführung
-   Logs und Diff abrufbar
-   Stop funktioniert
-   Cleanup entfernt Worktree
-   Core läuft stabil als Service

------------------------------------------------------------------------

## 8. Codex Arbeitsmodus

Jede Antwort endet mit:

A)  COMMANDS (copy/paste)\
B)  EXPECTED OUTPUT\
C)  FILES CHANGED\
D)  VERIFY\
E)  ROLLBACK

Stop if uncertain.

------------------------------------------------------------------------

End of Document
