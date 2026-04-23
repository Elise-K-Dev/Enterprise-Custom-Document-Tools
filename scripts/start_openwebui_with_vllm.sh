#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="/home/elise/Desktop/2026 Dev"
OPEN_WEBUI_DIR="${ROOT_DIR}/open-webui-main"
ENV_FILE="${ROOT_DIR}/Port-Project/open-webui/.env.openwebui"
BUILD_DIR="${ROOT_DIR}/Port-Project/.openwebui-build"

HOST_PORT="${HOST_PORT:-3000}"
IMAGE_NAME="${IMAGE_NAME:-open-webui-vllm-local}"
DOCKER_BIN="${DOCKER_BIN:-$(command -v docker || true)}"

if [[ ! -d "${OPEN_WEBUI_DIR}" ]]; then
  echo "[ERROR] Open WebUI source not found: ${OPEN_WEBUI_DIR}"
  exit 1
fi

if [[ ! -f "${ENV_FILE}" ]]; then
  echo "[ERROR] Open WebUI env file not found: ${ENV_FILE}"
  exit 1
fi

if [[ -z "${DOCKER_BIN}" ]]; then
  echo "[ERROR] docker command not found in PATH"
  echo "[HINT] Run this script from a shell where Docker works, or set DOCKER_BIN=/path/to/docker"
  exit 1
fi

cd "${OPEN_WEBUI_DIR}"

rm -rf "${BUILD_DIR}"
mkdir -p "${BUILD_DIR}"
cp -r . "${BUILD_DIR}/"
python3 - <<'PY'
from pathlib import Path

dockerfile = Path("/home/elise/Desktop/2026 Dev/Port-Project/.openwebui-build/Dockerfile")
text = dockerfile.read_text(encoding="utf-8")
text = text.replace(
    "FROM --platform=$BUILDPLATFORM node:22-alpine3.20 AS build",
    "FROM node:22-alpine3.20 AS build",
    1,
)
dockerfile.write_text(text, encoding="utf-8")
PY

cd "${BUILD_DIR}"
"${DOCKER_BIN}" build -t "${IMAGE_NAME}" .

echo "[INFO] Starting Port-Project services..."
cd "${ROOT_DIR}/Port-Project"
OPEN_WEBUI_IMAGE="${IMAGE_NAME}" "${DOCKER_BIN}" compose up -d --build

echo "[INFO] Waiting for Open WebUI health..."
for _ in $(seq 1 90); do
  if curl -fsS "http://127.0.0.1:${HOST_PORT}/health" >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

echo "[INFO] Syncing Open WebUI runtime state..."
"${ROOT_DIR}/Port-Project/scripts/sync_openwebui_runtime.sh"

echo "[INFO] Open WebUI started: http://127.0.0.1:${HOST_PORT}"
echo "[INFO] vLLM backend: http://192.168.100.13:8000/v1"
