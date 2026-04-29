#!/usr/bin/env sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PROJECT_DIR=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
ENV_FILE="$PROJECT_DIR/.env"

if [ -f "$ENV_FILE" ]; then
  set -a
  # shellcheck disable=SC1090
  . "$ENV_FILE"
  set +a
fi

if [ -z "${PORT_PROJECT_INTERNAL_TOKEN:-}" ]; then
  echo "[ERROR] PORT_PROJECT_INTERNAL_TOKEN is required. Add it to $ENV_FILE or export it."
  exit 1
fi

echo "[INFO] Starting Speaki service on :8006"
cd "$PROJECT_DIR/speaki-service"
SPEAKI_SERVICE_HOST=0.0.0.0 \
SPEAKI_SERVICE_PORT=8006 \
SPEAKI_SERVICE_PUBLIC_BASE_URL="${SPEAKI_SERVICE_PUBLIC_BASE_URL:-http://127.0.0.1:8006}" \
PORT_PROJECT_INTERNAL_TOKEN="$PORT_PROJECT_INTERNAL_TOKEN" \
uvicorn app.main:app --host 0.0.0.0 --port 8006
