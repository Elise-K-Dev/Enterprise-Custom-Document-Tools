from __future__ import annotations

import os
import secrets
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware

from fallback import build_fallback_response
from gemma_client import call_gemma
from models import WonConfirmRequest, WonConfirmResponse
from scoring import fixed_assessment
from utils import validate_and_merge_llm_output


app = FastAPI(title="won-confirm", version="0.1.0", openapi_url=None)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

OPEN_WEBUI_USER_EMAIL_HEADER = "x-openwebui-user-email"
OPEN_WEBUI_USER_ID_HEADER = "x-openwebui-user-id"
OPEN_WEBUI_USER_NAME_HEADER = "x-openwebui-user-name"
INTERNAL_TOKEN_HEADER = "x-port-project-internal-token"


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/tools/won-confirm", response_model=WonConfirmResponse)
async def won_confirm(request: WonConfirmRequest, raw_request: Request) -> WonConfirmResponse:
    require_allowed_account(raw_request)
    fixed = fixed_assessment(request.text, request.mode)
    try:
        llm_output = await call_gemma(request, fixed)
        return validate_and_merge_llm_output(llm_output, fixed, request)
    except Exception:
        return build_fallback_response(request, fixed)


def require_allowed_account(raw_request: Request) -> None:
    require_internal_request(raw_request)
    email = (raw_request.headers.get(OPEN_WEBUI_USER_EMAIL_HEADER) or "").strip().lower()
    user_id = (raw_request.headers.get(OPEN_WEBUI_USER_ID_HEADER) or "").strip()
    name = (raw_request.headers.get(OPEN_WEBUI_USER_NAME_HEADER) or "").strip().casefold()

    allowed_emails = _csv_env("WON_CONFIRM_ALLOWED_EMAILS", "elise@local.dev,sock@local.dev")
    allowed_names = _csv_env("WON_CONFIRM_ALLOWED_NAMES", "elise,Sock")
    allowed_ids = _csv_env("WON_CONFIRM_ALLOWED_USER_IDS", "")

    email_allowed = bool(email) and email in {item.lower() for item in allowed_emails}
    name_allowed = bool(name) and name in {item.casefold() for item in allowed_names}
    id_allowed = bool(user_id) and any(secrets.compare_digest(user_id, item) for item in allowed_ids)

    if email_allowed or name_allowed or id_allowed:
        return

    # Deliberately hide the tool from non-allowed accounts and direct callers.
    raise HTTPException(status_code=404, detail="not found")


def require_internal_request(raw_request: Request) -> None:
    expected = os.getenv("PORT_PROJECT_INTERNAL_TOKEN", "").strip()
    if not expected:
        return
    supplied = (raw_request.headers.get(INTERNAL_TOKEN_HEADER) or "").strip()
    if not secrets.compare_digest(supplied, expected):
        raise HTTPException(status_code=404, detail="not found")


def _csv_env(name: str, default: str) -> set[str]:
    raw = os.getenv(name, default)
    return {item.strip() for item in raw.split(",") if item.strip()}


@app.get("/openapi.json")
def openapi_spec(raw_request: Request) -> dict[str, Any]:
    require_internal_request(raw_request)
    return {
        "openapi": "3.0.0",
        "info": {
            "title": "won-confirm",
            "version": "0.1.0",
            "description": (
                "사용자가 입력한 계획서, 보고서, 아이디어를 규칙 기반으로 채점하고 "
                "Gemma 4를 이용해 교수 스타일 피드백을 생성하는 OpenWebUI 도구입니다. "
                "판정, 점수, weakest_area는 항상 코드가 계산합니다."
            ),
        },
        "servers": [{"url": "http://won-confirm-service:8010"}],
        "paths": {
            "/health": {
                "get": {
                    "operationId": "won_confirm_health_check",
                    "summary": "Health check",
                    "responses": {
                        "200": {
                            "description": "Healthy",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {"status": {"type": "string", "example": "ok"}},
                                    }
                                }
                            },
                        }
                    },
                }
            },
            "/tools/won-confirm": {
                "post": {
                    "operationId": "won_confirm_review",
                    "summary": "계획서/보고서/아이디어 텍스트를 규칙 기반으로 검토",
                    "description": (
                        "입력 텍스트를 purpose, scope, feasibility, validation, deliverable, logic "
                        "6개 항목으로 채점하고, 고정 판정과 가장 약한 영역을 기준으로 교수 스타일 "
                        "피드백을 반환합니다. LLM 실패 시에도 규칙 기반 fallback을 반환합니다."
                    ),
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["text"],
                                    "properties": {
                                        "text": {"type": "string", "description": "검토할 텍스트"},
                                        "mode": {
                                            "type": "string",
                                            "enum": ["normal", "strict", "savage", "roast"],
                                            "default": "normal",
                                        },
                                        "rewrite": {"type": "boolean", "default": True},
                                    },
                                }
                            }
                        },
                    },
                    "responses": {"200": {"description": "Review result"}},
                }
            },
        },
    }
