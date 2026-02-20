#!/usr/bin/env bash
set -euo pipefail

TAILSCALE_IP="${TAILSCALE_IP:-}"
PORT="${PORT:-8088}"

if [ -z "$TAILSCALE_IP" ]; then
  echo "TAILSCALE_IP is required" >&2
  exit 1
fi

if [[ "$TAILSCALE_IP" != 100.* ]]; then
  echo "TAILSCALE_IP must look like a Tailscale CGNAT IP (100.x.x.x)" >&2
  exit 2
fi

python3 -m http.server "$PORT" --bind "$TAILSCALE_IP" --directory ui
