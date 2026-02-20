# Reliability (P1)

- Retry/Timeout fuer externe Adapter
- Idempotenz fuer approve/stop
- Crash-safe Cleanup beim Service-Neustart

Referenz-Implementierung:
- `core/reliability.py` (retry wrapper)
- `runner/tmux_driver.py` (tmux timeout + retry)
- `core/idempotency.py` und `core/engine.py` (idempotente action_id Verarbeitung)
- `core/startup_recovery.py` (stale worktree cleanup beim Start)
