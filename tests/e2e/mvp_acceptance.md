# MVP Acceptance Test

## Ziel
Prueft die Definition of Done fuer das MVP.

## Testfaelle
1. Matrix-Job erstellt isolierten Worktree.
2. Approval via Reaction startet Ausfuehrung.
3. Logs und Diff sind abrufbar.
4. Stop beendet laufenden Job.
5. Cleanup entfernt Worktree.

## Lokaler Smoke-Run
```bash
scripts/mvp_smoke.sh
```

Der Smoke-Run prueft aktuell:
- Reaction `✅` wird akzeptiert und setzt State auf `RUNNING`.
- Audit-Log wird geschrieben (`approve`, `allowed=true`).
- Unit-Tests laufen erfolgreich.

Hinweis:
- Voller Realbetrieb (tmux-Session + `/srv` Worktrees) braucht eine Zielumgebung mit tmux und Repos unter `/srv/repos`.
