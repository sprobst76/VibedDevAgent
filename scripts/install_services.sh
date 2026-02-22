#!/usr/bin/env bash
# install_services.sh — install/update systemd service units from the Git repo
# Usage: sudo bash scripts/install_services.sh
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"

if [[ "${EUID}" -ne 0 ]]; then
  echo "ERROR: run as root: sudo bash scripts/install_services.sh" >&2
  exit 1
fi

echo "==> Installing service units from ${REPO_DIR}"
install -m 0644 "${REPO_DIR}/ops/systemd/devagent.service"    /etc/systemd/system/devagent.service
install -m 0644 "${REPO_DIR}/ops/systemd/devagent-ui.service" /etc/systemd/system/devagent-ui.service

echo "==> Reloading systemd"
systemctl daemon-reload

echo "==> Restarting services"
systemctl restart devagent devagent-ui

echo "==> Status"
sleep 2
systemctl --no-pager -l status devagent devagent-ui | grep -E "Active:|Main PID:|Error|INFO|ERROR" | head -20
