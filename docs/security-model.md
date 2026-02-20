# Security Model (MVP)

- Default ist read-only bis expliziter Approval.
- Nur allowlisted User duerfen `approve/reject/stop` ausloesen.
- Writes/Exec nur nach erfolgreichem Approval-Gate.
- Jede Aktion wird mit user, timestamp und job_id auditiert.

Referenz-Implementierung:
- `core/security.py` (Allowlist Parsing + Check)
- `adapters/matrix/reactions.py` (State- und Rechtepruefung)
- `core/audit.py` (Audit JSONL je Job)
