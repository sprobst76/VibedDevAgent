# Tailscale-only UI Access

## Ziel
Das Dashboard soll nur im Tailscale-Netz erreichbar sein.

## Start (lokal auf VPS)
```bash
TAILSCALE_IP=100.64.0.10 bash ops/tailscale/serve-ui.sh
```

## Verhalten
- Der Server bindet nur auf `TAILSCALE_IP`.
- Ohne `TAILSCALE_IP` startet der Server nicht.
