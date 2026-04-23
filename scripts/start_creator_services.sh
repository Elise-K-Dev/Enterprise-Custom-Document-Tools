#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="/home/elise/Desktop/2026 Dev/Port-Project"

echo "[INFO] Starting Rust creator API on :8001"
(
  cd "${ROOT_DIR}/rust-service"
  DOCUMENT_SERVICE_HOST=0.0.0.0 DOCUMENT_SERVICE_PORT=8001 cargo run
)
