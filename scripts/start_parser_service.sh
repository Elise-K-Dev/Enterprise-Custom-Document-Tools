#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="/home/elise/Desktop/2026 Dev/Port-Project"

cd "${ROOT_DIR}/python-service"

if [[ ! -d .venv ]]; then
  python3 -m venv .venv
fi

source .venv/bin/activate
pip install -e .
PARSER_SERVICE_HOST=0.0.0.0 PARSER_SERVICE_PORT=8002 uvicorn app.main:app --host 0.0.0.0 --port 8002
