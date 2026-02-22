#!/usr/bin/env bash
# deploy.sh — Sync dev tree → /srv/devagent and restart services
set -euo pipefail

SRC="/home/spro/development/VibedDevAgent"
DST="/srv/devagent"

echo "==> Deploying $SRC → $DST"

# Core worker
cp "$SRC/core/matrix_worker.py"          "$DST/core/matrix_worker.py"

# Matrix adapter
cp "$SRC/adapters/matrix/client.py"      "$DST/adapters/matrix/client.py"
cp "$SRC/adapters/matrix/ai_handler.py"  "$DST/adapters/matrix/ai_handler.py"
cp "$SRC/adapters/matrix/reactions.py"   "$DST/adapters/matrix/reactions.py"

# UI
cp "$SRC/ui/server.py"                   "$DST/ui/server.py"
cp "$SRC/ui/projects_registry.py"        "$DST/ui/projects_registry.py"
cp -r "$SRC/ui/templates/"               "$DST/ui/templates/"

echo "==> Restarting services"
sudo systemctl restart devagent.service devagent-ui.service

echo "==> Status"
sudo systemctl status devagent.service devagent-ui.service --no-pager -l
