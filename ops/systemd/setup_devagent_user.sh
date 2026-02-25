#!/usr/bin/env bash
# setup_devagent_user.sh — Migrate DevAgent services to the dedicated devagent system user.
#
# Run as: sudo bash ops/systemd/setup_devagent_user.sh
# Safe to re-run.
#
# What this does:
#   1. Fix /srv/devagent/ ownership → devagent:devagent, .env chmod 640
#   2. Fix /var/log/devagent/ ownership + existing log files
#   3. Install claude CLI to /usr/local/bin/ (world-executable)
#   4. Give devagent read access to project dir via POSIX ACLs
#   5. Deploy updated service files and restart

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
DEVAGENT_USER="devagent"
CLAUDE_SRC="/home/spro/.local/share/claude/versions/2.1.58"
CLAUDE_DST="/usr/local/bin/claude"

echo "==> DevAgent user setup"
echo "    Project:  $PROJECT_DIR"
echo "    Run user: $DEVAGENT_USER"
echo ""

# ── 1. /srv/devagent/ ownership ───────────────────────────────────────────────
echo "[1/6] Fixing /srv/devagent/ ownership..."
chown -R "$DEVAGENT_USER:$DEVAGENT_USER" /srv/devagent/
chmod 640 /srv/devagent/.env
echo "      /srv/devagent/ → $DEVAGENT_USER:$DEVAGENT_USER"
echo "      .env → 640 (devagent can read, others cannot)"

# ── 2. /var/log/devagent/ ─────────────────────────────────────────────────────
echo "[2/6] Fixing /var/log/devagent/ ownership..."
chown "$DEVAGENT_USER:$DEVAGENT_USER" /var/log/devagent/
# Fix existing log files (may be owned by root from previous run as spro/root)
for f in /var/log/devagent/*.log; do
    [ -f "$f" ] && chown "$DEVAGENT_USER:$DEVAGENT_USER" "$f" && echo "      $f → $DEVAGENT_USER"
done

# ── 3. claude CLI → /usr/local/bin/ + credentials ────────────────────────────
echo "[3/6] Installing claude CLI globally..."
if [ ! -f "$CLAUDE_SRC" ]; then
    echo "      WARNING: $CLAUDE_SRC not found — skipping."
    echo "      Update CLAUDE_SRC in this script if claude was installed elsewhere."
    echo "      Or set DEVAGENT_CLAUDE_BIN in .env to the correct path."
else
    cp "$CLAUDE_SRC" "$CLAUDE_DST"
    chmod 755 "$CLAUDE_DST"
    echo "      $CLAUDE_DST installed ($(du -sh "$CLAUDE_DST" | cut -f1))"
    echo "      NOTE: Re-run this step after 'claude update' to keep in sync."
fi

# ── 3b. Claude credentials for devagent user ──────────────────────────────────
echo "[3b/6] Copying Claude credentials to devagent home..."
CREDS_SRC="/home/spro/.claude/.credentials.json"
CREDS_DST="/srv/devagent/.claude/.credentials.json"
if [ ! -f "$CREDS_SRC" ]; then
    echo "      WARNING: $CREDS_SRC not found — skipping."
    echo "      devagent user will not be able to run the claude CLI."
else
    mkdir -p /srv/devagent/.claude
    cp "$CREDS_SRC" "$CREDS_DST"
    chown -R "$DEVAGENT_USER:$DEVAGENT_USER" /srv/devagent/.claude
    chmod 700 /srv/devagent/.claude
    chmod 600 "$CREDS_DST"
    echo "      $CREDS_DST installed (devagent:devagent, mode 600)"
    echo "      NOTE: Re-run this step after re-authenticating with 'claude' to keep in sync."
fi

# ── 4. POSIX ACL: devagent read+write-access to project dir ──────────────────
echo "[4/6] Setting ACLs for project directory access..."
# Traverse-only on parent dirs (x only, no r — cannot list /home/spro)
setfacl -m "u:$DEVAGENT_USER:x" /home/spro
setfacl -m "u:$DEVAGENT_USER:x" /home/spro/development
echo "      /home/spro, /home/spro/development → traverse (x)"

# Read + write + execute on the entire development directory (all projects).
# Write access is required so devagent can edit files and run git commits in
# registered projects. Default ACL ensures new files are also accessible.
setfacl -R -m "u:$DEVAGENT_USER:rwX" /home/spro/development/
setfacl -R -d -m "u:$DEVAGENT_USER:rwX" /home/spro/development/
echo "      /home/spro/development/** → rwX (recursive + default)"

# ── 5. Deploy service files ───────────────────────────────────────────────────
echo "[5/6] Deploying service files..."
SYSTEMD_DIR="/etc/systemd/system"

for svc in devagent.service devagent-ui.service; do
    src="$SCRIPT_DIR/$svc"
    if [ -f "$src" ]; then
        cp "$src" "$SYSTEMD_DIR/$svc"
        echo "      $svc deployed"
    else
        echo "      WARNING: $src not found — skipping $svc"
    fi
done

systemctl daemon-reload
echo "      systemd daemon reloaded"

# ── 6. Restart services ───────────────────────────────────────────────────────
echo "[6/6] Restarting services..."
systemctl restart devagent || echo "      WARNING: devagent restart failed (check journalctl -u devagent)"
sleep 2
systemctl restart devagent-ui || echo "      WARNING: devagent-ui restart failed"

echo ""
echo "==> Done. Checking status..."
echo ""
systemctl status devagent --no-pager -l | head -12
echo ""
systemctl status devagent-ui --no-pager -l | head -12
echo ""
echo "Verify with:"
echo "  journalctl -u devagent -n 30 --no-pager"
echo "  journalctl -u devagent-ui -n 30 --no-pager"
