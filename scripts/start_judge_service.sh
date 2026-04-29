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

if [ -z "${GEMMA_API_URL:-}" ]; then
  echo "[ERROR] GEMMA_API_URL is required. Add it to $ENV_FILE or export it."
  exit 1
fi

echo "[INFO] Starting Judge service on :8010"
cd "$PROJECT_DIR/judge-service"
JUDGE_HOST=0.0.0.0 \
JUDGE_PORT=8010 \
GEMMA_API_URL="$GEMMA_API_URL" \
GEMMA_MODEL="${GEMMA_MODEL:-gemma-4-31b-it}" \
GEMMA_API_KEY="${GEMMA_API_KEY:-}" \
GEMMA_TIMEOUT_SECONDS="${GEMMA_TIMEOUT_SECONDS:-30}" \
PORT_PROJECT_INTERNAL_TOKEN="${PORT_PROJECT_INTERNAL_TOKEN:-}" \
JUDGE_ALLOWED_EMAILS="${JUDGE_ALLOWED_EMAILS:-elise@local.dev,sock@gmail.com}" \
JUDGE_ALLOWED_NAMES="${JUDGE_ALLOWED_NAMES:-elise,Sock}" \
JUDGE_ALLOWED_USER_IDS="${JUDGE_ALLOWED_USER_IDS:-}" \
uvicorn app:app --host 0.0.0.0 --port 8010
