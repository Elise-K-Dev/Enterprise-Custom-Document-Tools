#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DOCKER_BIN="${DOCKER_BIN:-$(command -v docker || true)}"

CLIENT_ADMIN_EMAIL="${CLIENT_ADMIN_EMAIL:-admin@gmail.com}"
CLIENT_ADMIN_PASSWORD="${CLIENT_ADMIN_PASSWORD:-0000}"
CLIENT_ADMIN_NAME="${CLIENT_ADMIN_NAME:-admin}"
STANDARD_USER_EMAIL="${STANDARD_USER_EMAIL:-user@gmail.com}"
STANDARD_USER_PASSWORD="${STANDARD_USER_PASSWORD:-0000}"
STANDARD_USER_NAME="${STANDARD_USER_NAME:-user}"
DEVELOPER_EMAIL="${DEVELOPER_EMAIL:-elise@local.dev}"
DEVELOPER_PASSWORD="${DEVELOPER_PASSWORD:-Wis_08171!}"
DEVELOPER_NAME="${DEVELOPER_NAME:-elise}"
RUST_TOOL_SERVER_URL="${RUST_TOOL_SERVER_URL:-http://document-service:8001}"
PARSER_TOOL_SERVER_URL="${PARSER_TOOL_SERVER_URL:-http://parser-service:8002}"
DOCUMENT_FILLER_MODEL_ID="${DOCUMENT_FILLER_MODEL_ID:-gemma-4-31b-it}"

if [[ -z "${DOCKER_BIN}" ]]; then
  echo "[ERROR] docker command not found in PATH"
  exit 1
fi

cd "${ROOT_DIR}"

"${DOCKER_BIN}" compose exec -T \
  -e CLIENT_ADMIN_EMAIL="${CLIENT_ADMIN_EMAIL}" \
  -e CLIENT_ADMIN_PASSWORD="${CLIENT_ADMIN_PASSWORD}" \
  -e CLIENT_ADMIN_NAME="${CLIENT_ADMIN_NAME}" \
  -e STANDARD_USER_EMAIL="${STANDARD_USER_EMAIL}" \
  -e STANDARD_USER_PASSWORD="${STANDARD_USER_PASSWORD}" \
  -e STANDARD_USER_NAME="${STANDARD_USER_NAME}" \
  -e DEVELOPER_EMAIL="${DEVELOPER_EMAIL}" \
  -e DEVELOPER_PASSWORD="${DEVELOPER_PASSWORD}" \
  -e DEVELOPER_NAME="${DEVELOPER_NAME}" \
  -e RUST_TOOL_SERVER_URL="${RUST_TOOL_SERVER_URL}" \
  -e PARSER_TOOL_SERVER_URL="${PARSER_TOOL_SERVER_URL}" \
  -e DOCUMENT_FILLER_MODEL_ID="${DOCUMENT_FILLER_MODEL_ID}" \
  open-webui \
  sh -lc '
WEBUI_SECRET_KEY_VALUE=""
if [ -f /app/backend/.webui_secret_key ]; then
  WEBUI_SECRET_KEY_VALUE="$(cat /app/backend/.webui_secret_key)"
fi
export WEBUI_SECRET_KEY="${WEBUI_SECRET_KEY_VALUE}"
cd /app/backend
python - <<'"'"'PY'"'"'
import asyncio
import os
from typing import Any, Dict, List

from open_webui.config import DEFAULT_MODEL_METADATA, DEFAULT_MODELS, TOOL_SERVER_CONNECTIONS, USER_PERMISSIONS
from open_webui.models.auths import Auths
from open_webui.models.users import Users
from open_webui.utils.auth import get_password_hash


async def ensure_user(email: str, password: str, name: str, role: str) -> None:
    user = await Users.get_user_by_email(email)
    hashed = get_password_hash(password)

    if user:
        await Users.update_user_by_id(user.id, {"email": email, "name": name})
        await Users.update_user_role_by_id(user.id, role)
        await Auths.update_user_password_by_id(user.id, hashed)
        print(f"[INFO] Updated Open WebUI user: {email} ({role})")
        return

    created = await Auths.insert_new_auth(
        email=email,
        password=hashed,
        name=name,
        role=role,
    )
    if created:
        print(f"[INFO] Created Open WebUI user: {email} ({role})")
    else:
        raise RuntimeError(f"Failed to create Open WebUI user: {email}")


async def sync_user_settings(email: str, model_id: str, function_calling: str = "native") -> None:
    user = await Users.get_user_by_email(email)
    if not user:
        raise RuntimeError(f"User not found for settings sync: {email}")

    settings = dict(user.settings or {})
    settings["models"] = [model_id]
    settings["system"] = ""
    settings["model"] = model_id

    params = dict(settings.get("params") or {})
    params["function_calling"] = function_calling
    params.pop("system", None)
    settings["params"] = params

    ui = dict(settings.get("ui") or {})
    settings["ui"] = ui

    await Users.update_user_by_id(user.id, {"settings": settings})
    print(f"[INFO] Synced Open WebUI user settings: {email}")


def upsert_tool_server(existing_servers: List[Dict[str, Any]], desired_server: Dict[str, Any]) -> None:
    desired_id = ((desired_server.get("info") or {}).get("id") or "").strip()
    if not desired_id:
        raise RuntimeError("desired tool server info.id is required")

    for idx, server in enumerate(existing_servers):
        server_id = ((server.get("info") or {}).get("id") or "").strip()
        if server_id == desired_id:
            preserved_config = dict(server.get("config") or {})
            desired_config = dict(desired_server.get("config") or {})
            if "access_grants" not in desired_config and "access_grants" in preserved_config:
                desired_config["access_grants"] = preserved_config["access_grants"]

            existing_servers[idx] = {
                **server,
                **desired_server,
                "info": {
                    **(server.get("info") or {}),
                    **(desired_server.get("info") or {}),
                },
                "config": {
                    **preserved_config,
                    **desired_config,
                },
            }
            return

    existing_servers.append(desired_server)


def sync_tool_servers() -> None:
    rust_tool_server_url = os.getenv("RUST_TOOL_SERVER_URL", "http://document-service:8001").rstrip("/")
    parser_tool_server_url = os.getenv("PARSER_TOOL_SERVER_URL", "http://parser-service:8002").rstrip("/")
    public_read_grants = [
        {
            "principal_type": "user",
            "principal_id": "*",
            "permission": "read",
        }
    ]
    desired_servers = [
        {
            "type": "openapi",
            "url": rust_tool_server_url,
            "spec_type": "url",
            "path": "/openapi.json",
            "auth_type": "none",
            "headers": None,
            "key": "",
            "info": {
                "id": "purchase_document_conversation_tools",
                "name": "Rust 문서 생성 도구",
                "description": "Rust 서비스의 자체 필드 추출과 문서 생성 로직으로 대화형 필드 채움, 품목 조회, 컨텍스트 준비, 단건 생성, 승인 생성, 전체 ZIP 생성을 수행합니다.",
            },
            "config": {
                "enable": True,
                "function_name_filter_list": "create_document,fill_document,export_document,list_shortage_items,get_item_document_context,export_single_item_document,approve_and_generate_item_document,generate_purchase_document_package",
                "access_grants": public_read_grants,
            },
        },
        {
            "type": "openapi",
            "url": parser_tool_server_url,
            "spec_type": "url",
            "path": "/openapi.json",
            "auth_type": "none",
            "headers": None,
            "key": "",
            "info": {
                "id": "document_search",
                "name": "Python 문서 검색",
                "description": "권한에 맞는 문서 검색과 답변 생성을 수행합니다.",
            },
            "config": {
                "enable": True,
                "function_name_filter_list": "search_documents_by_rank",
                "access_grants": public_read_grants,
            },
        },
    ]

    current = TOOL_SERVER_CONNECTIONS.value
    tool_servers = list(current) if isinstance(current, list) else []
    managed_server_ids = {
        "purchase_document_conversation_tools",
        "document_search",
        "legacy_md_search",
    }
    tool_servers = [
        server
        for server in tool_servers
        if ((server.get("info") or {}).get("id") or "").strip() not in managed_server_ids
    ]
    for desired in desired_servers:
        upsert_tool_server(tool_servers, desired)

    TOOL_SERVER_CONNECTIONS.value = tool_servers
    TOOL_SERVER_CONNECTIONS.save()
    print("[INFO] Synced Open WebUI tool servers")


def sync_default_model_metadata() -> None:
    current = DEFAULT_MODEL_METADATA.value
    metadata = dict(current) if isinstance(current, dict) else {}
    metadata["name"] = "Æ CDXVI Indexer"
    params = dict(metadata.get("params") or {})
    params["function_calling"] = "native"
    params["system"] = (
        "당신은 Open WebUI에서 도구 호출을 우선하는 재고/문서 보조 모델이다.\n"
        "중요 규칙:\n"
        "- 사용자가 현재 재고가 없는 품목, 부족 품목, 구매가 필요한 품목을 물으면 반드시 list_shortage_items 를 먼저 호출한다.\n"
        "- 사용자가 특정 품목이나 문서를 찾거나 근거 검색이 필요하면 search_documents_by_rank 를 호출한다.\n"
        "- 도구로 확인 가능한 내용은 추측하지 말고 먼저 도구를 호출한다.\n"
        "- 도구 결과가 비어 있거나 부족할 때만 그 사실을 설명하고 필요한 추가 조건을 짧게 질문한다."
    )
    metadata["params"] = params
    capabilities = dict(metadata.get("capabilities") or {})
    capabilities["builtin_tools"] = False
    metadata["capabilities"] = capabilities
    tool_ids = metadata.get("toolIds") or []
    if not isinstance(tool_ids, list):
        tool_ids = []

    obsolete_tool_ids = {
        "server:document_search",
        "server:purchase_document_conversation_tools",
        "server:legacy_md_search",
    }
    merged_tool_ids = []
    for tool_id in tool_ids:
        if tool_id not in obsolete_tool_ids and tool_id not in merged_tool_ids:
            merged_tool_ids.append(tool_id)

    for tool_id in [
        "server:document_search",
        "server:purchase_document_conversation_tools",
    ]:
        if tool_id not in merged_tool_ids:
            merged_tool_ids.append(tool_id)

    metadata["toolIds"] = merged_tool_ids
    DEFAULT_MODEL_METADATA.value = metadata
    DEFAULT_MODEL_METADATA.save()
    joined_tool_ids = ", ".join(merged_tool_ids)
    print(f"[INFO] Synced default model toolIds: {joined_tool_ids}")
    print("[INFO] Synced default model function calling: native")
    print("[INFO] Synced default model builtin_tools: false")


def sync_default_models() -> None:
    default_models = os.getenv("DOCUMENT_FILLER_MODEL_ID", "gemma-4-31b-it")
    DEFAULT_MODELS.value = default_models
    DEFAULT_MODELS.save()
    print(f"[INFO] Synced default models: {default_models}")


def sync_user_permissions() -> None:
    USER_PERMISSIONS.value = {
        "workspace": {
            "models": False,
            "knowledge": False,
            "prompts": False,
            "tools": False,
            "skills": False,
            "models_import": False,
            "models_export": False,
            "prompts_import": False,
            "prompts_export": False,
            "tools_import": False,
            "tools_export": False,
        },
        "sharing": {
            "models": False,
            "public_models": False,
            "knowledge": False,
            "public_knowledge": False,
            "prompts": False,
            "public_prompts": False,
            "tools": False,
            "public_tools": False,
            "skills": False,
            "public_skills": False,
            "notes": False,
            "public_notes": False,
        },
        "access_grants": {
            "allow_users": True,
        },
        "chat": {
            "controls": True,
            "valves": True,
            "system_prompt": False,
            "params": False,
            "file_upload": True,
            "web_upload": True,
            "delete": True,
            "delete_message": True,
            "continue_response": True,
            "regenerate_response": True,
            "rate_response": True,
            "edit": True,
            "share": True,
            "export": True,
            "stt": True,
            "tts": True,
            "call": True,
            "multiple_models": False,
            "temporary": True,
            "temporary_enforced": False,
        },
        "features": {
            "api_keys": False,
            "notes": True,
            "channels": True,
            "folders": True,
            "direct_tool_servers": False,
            "web_search": True,
            "image_generation": True,
            "code_interpreter": True,
            "memories": True,
            "automations": False,
            "calendar": True,
        },
        "settings": {
            "interface": True,
        },
    }
    USER_PERMISSIONS.save()
    print("[INFO] Synced restricted default user permissions")


async def main() -> None:
    model_id = os.getenv("DOCUMENT_FILLER_MODEL_ID", "gemma-4-31b-it")

    await ensure_user(
        os.environ["DEVELOPER_EMAIL"],
        os.environ["DEVELOPER_PASSWORD"],
        os.environ["DEVELOPER_NAME"],
        "admin",
    )
    await ensure_user(
        os.environ["CLIENT_ADMIN_EMAIL"],
        os.environ["CLIENT_ADMIN_PASSWORD"],
        os.environ["CLIENT_ADMIN_NAME"],
        "user",
    )
    await ensure_user(
        os.environ["STANDARD_USER_EMAIL"],
        os.environ["STANDARD_USER_PASSWORD"],
        os.environ["STANDARD_USER_NAME"],
        "user",
    )
    await sync_user_settings(os.environ["DEVELOPER_EMAIL"], model_id)
    await sync_user_settings(os.environ["CLIENT_ADMIN_EMAIL"], model_id)
    await sync_user_settings(os.environ["STANDARD_USER_EMAIL"], model_id)
    sync_tool_servers()
    sync_default_models()
    sync_default_model_metadata()
    sync_user_permissions()


asyncio.run(main())
PY
'
