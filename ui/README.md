# Web UI (P2)

Read-only Dashboard fuer Jobstatus, Logs und Diff.
Zugriff ausschliesslich via Tailscale (on-demand).

Implementiert in:
- `ui/index.html` (statisches Dashboard)
- `ui/generate_dashboard_data.py` (erzeugt `ui/jobs.json` aus Audit-Logs)
