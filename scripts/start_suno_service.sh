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
  echo "[ERROR] PORT_PROJECT_INTERNAL_TOKEN is required. Run scripts/start_openwebui_with_vllm.sh once or add it to $ENV_FILE."
  exit 1
fi

echo "[INFO] Starting Suno service on :8005"
cd "$PROJECT_DIR/suno-service"
SUNO_SERVICE_HOST=0.0.0.0 \
SUNO_SERVICE_PORT=8005 \
SUNO_SERVICE_PUBLIC_BASE_URL="${SUNO_SERVICE_PUBLIC_BASE_URL:-http://127.0.0.1:8005}" \
SUNO_LLM_API_URL="${SUNO_LLM_API_URL:-http://192.168.100.13:8000/v1/chat/completions}" \
SUNO_LLM_MODEL_ID="${SUNO_LLM_MODEL_ID:-gemma-4-31b-it}" \
SUNO_TEMPLATE_DIR="${SUNO_TEMPLATE_DIR:-$PROJECT_DIR/suno-service/templates}" \
SUNO_GUIDE_HOT_RELOAD="${SUNO_GUIDE_HOT_RELOAD:-true}" \
PORT_PROJECT_INTERNAL_TOKEN="$PORT_PROJECT_INTERNAL_TOKEN" \
uvicorn app.main:app --host 0.0.0.0 --port 8005
