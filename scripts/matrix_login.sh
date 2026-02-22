#!/usr/bin/env bash
# matrix_login.sh — Login to Matrix and update MATRIX_ACCESS_TOKEN in .env
#
# Usage:
#   ./scripts/matrix_login.sh operator    # login as the operator account (MATRIX_USER_OPERATOR)
#   ./scripts/matrix_login.sh devagent    # login as the bot account (MATRIX_USER_DEVAGENT)
#   ./scripts/matrix_login.sh             # interactive: choose user

set -euo pipefail

ENV_FILE="/srv/devagent/.env"

# ── Load .env ─────────────────────────────────────────────────────────────────
if [[ ! -f "$ENV_FILE" ]]; then
  echo "ERROR: $ENV_FILE not found" >&2
  exit 1
fi
set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

# ── Select user ───────────────────────────────────────────────────────────────
PROFILE="${1:-}"

if [[ -z "$PROFILE" ]]; then
  echo "Welcher User?"
  echo "  1) operator  ($MATRIX_USER_OPERATOR)"
  echo "  2) devagent  ($MATRIX_USER_DEVAGENT)"
  read -rp "Auswahl [1/2]: " choice
  case "$choice" in
    1) PROFILE="operator" ;;
    2) PROFILE="devagent" ;;
    *) echo "Ungültige Auswahl" >&2; exit 1 ;;
  esac
fi

case "$PROFILE" in
  operator)
    MATRIX_USER="$MATRIX_USER_OPERATOR"
    MATRIX_PASSWORD="${MATRIX_PASSWORD_OPERATOR:-}"
    ;;
  devagent)
    MATRIX_USER="$MATRIX_USER_DEVAGENT"
    MATRIX_PASSWORD="${MATRIX_PASSWORD_DEVAGENT:-}"
    ;;
  *)
    echo "ERROR: Unbekanntes Profil '$PROFILE'. Nutze: operator | devagent" >&2
    exit 1
    ;;
esac

# ── Prompt for password if not set ────────────────────────────────────────────
if [[ -z "$MATRIX_PASSWORD" ]]; then
  read -rsp "Passwort für $MATRIX_USER: " MATRIX_PASSWORD
  echo
fi

# ── Login ─────────────────────────────────────────────────────────────────────
echo "==> Logging in as $MATRIX_USER ..."

RESPONSE=$(curl -s -X POST "${MATRIX_HOMESERVER_URL}/_matrix/client/v3/login" \
  -H "Content-Type: application/json" \
  -d "{\"type\":\"m.login.password\",\"user\":\"${MATRIX_USER}\",\"password\":\"${MATRIX_PASSWORD}\"}")

# Check for error
if echo "$RESPONSE" | python3 -c "import sys,json; d=json.load(sys.stdin); sys.exit(0 if 'access_token' in d else 1)" 2>/dev/null; then
  ACCESS_TOKEN=$(echo "$RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")
  DEVICE_ID=$(echo "$RESPONSE"    | python3 -c "import sys,json; print(json.load(sys.stdin).get('device_id','?'))")
  REFRESH_TOKEN=$(echo "$RESPONSE" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('refresh_token',''))" 2>/dev/null || true)
else
  ERROR=$(echo "$RESPONSE" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('error','unknown error'))" 2>/dev/null || echo "$RESPONSE")
  echo "ERROR: Login fehlgeschlagen — $ERROR" >&2
  exit 1
fi

echo "==> Login OK (device: $DEVICE_ID)"
if [[ -n "$REFRESH_TOKEN" ]]; then
  echo "    refresh_token vorhanden"
fi

# ── Update .env ───────────────────────────────────────────────────────────────
echo "==> Aktualisiere MATRIX_ACCESS_TOKEN in $ENV_FILE ..."
sed -i "s|^MATRIX_ACCESS_TOKEN=.*|MATRIX_ACCESS_TOKEN=${ACCESS_TOKEN}|" "$ENV_FILE"

if [[ -n "$REFRESH_TOKEN" ]]; then
  if grep -q "^MATRIX_REFRESH_TOKEN=" "$ENV_FILE"; then
    sed -i "s|^MATRIX_REFRESH_TOKEN=.*|MATRIX_REFRESH_TOKEN=${REFRESH_TOKEN}|" "$ENV_FILE"
  else
    sed -i "/^MATRIX_ACCESS_TOKEN=/a MATRIX_REFRESH_TOKEN=${REFRESH_TOKEN}" "$ENV_FILE"
  fi
fi

echo "==> Token aktualisiert."
echo ""
echo "Services neu starten?"
read -rp "[j/N]: " restart
if [[ "$restart" =~ ^[jJyY]$ ]]; then
  sudo systemctl restart devagent.service devagent-ui.service
  echo "==> Services neu gestartet."
  sleep 2
  sudo systemctl status devagent.service devagent-ui.service --no-pager -l | grep -E "Active:|ERROR|INFO" | head -10
fi
