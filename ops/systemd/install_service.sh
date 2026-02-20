#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-/srv/devagent}"
SERVICE_SRC="${PROJECT_DIR}/ops/systemd/devagent.service"
SERVICE_DST="/etc/systemd/system/devagent.service"
ENV_SRC="${PROJECT_DIR}/.env"
STATE_DIR="${PROJECT_DIR}/state"

if [ "${EUID}" -ne 0 ]; then
  echo "Run as root: sudo bash ops/systemd/install_service.sh" >&2
  exit 1
fi

if [ ! -f "$SERVICE_SRC" ]; then
  echo "Missing service file: $SERVICE_SRC" >&2
  exit 2
fi

if [ ! -f "$ENV_SRC" ]; then
  echo "Missing env file: $ENV_SRC" >&2
  echo "Create it first (copy from .env.example and fill real values)." >&2
  exit 3
fi

mkdir -p "$STATE_DIR"
chown -R devagent:devagent "$STATE_DIR" || true

install -m 0644 "$SERVICE_SRC" "$SERVICE_DST"
systemctl daemon-reload
systemctl enable --now devagent
systemctl reset-failed devagent || true

systemctl --no-pager --full status devagent | sed -n '1,30p'
