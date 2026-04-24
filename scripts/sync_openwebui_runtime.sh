#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DOCKER_BIN="${DOCKER_BIN:-$(command -v docker || true)}"
ENV_FILE="${ROOT_DIR}/.env"
COMPOSE_ARGS=(-f docker-compose.yml -f docker-compose.host.yml)

if [[ -f "${ENV_FILE}" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "${ENV_FILE}"
  set +a
fi

CLIENT_ADMIN_EMAIL="${CLIENT_ADMIN_EMAIL:-admin@gmail.com}"
CLIENT_ADMIN_PASSWORD="${CLIENT_ADMIN_PASSWORD:-0000}"
CLIENT_ADMIN_NAME="${CLIENT_ADMIN_NAME:-admin}"
STANDARD_USER_EMAIL="${STANDARD_USER_EMAIL:-user@gmail.com}"
STANDARD_USER_PASSWORD="${STANDARD_USER_PASSWORD:-0000}"
STANDARD_USER_NAME="${STANDARD_USER_NAME:-user}"
DEVELOPER_EMAIL="${DEVELOPER_EMAIL:-elise@local.dev}"
DEVELOPER_PASSWORD="${DEVELOPER_PASSWORD:-Wis_08171!}"
DEVELOPER_NAME="${DEVELOPER_NAME:-elise}"
RUST_TOOL_SERVER_URL="${RUST_TOOL_SERVER_URL:-http://127.0.0.1:8001}"
PARSER_TOOL_SERVER_URL="${PARSER_TOOL_SERVER_URL:-http://127.0.0.1:8002}"
DOCUMENT_FILLER_MODEL_ID="${DOCUMENT_FILLER_MODEL_ID:-gemma-4-31b-it}"
PORT_PROJECT_INTERNAL_TOKEN="${PORT_PROJECT_INTERNAL_TOKEN:-}"

if [[ -z "${DOCKER_BIN}" ]]; then
  echo "[ERROR] docker command not found in PATH"
  exit 1
fi

if [[ -z "${PORT_PROJECT_INTERNAL_TOKEN}" ]]; then
  echo "[ERROR] PORT_PROJECT_INTERNAL_TOKEN is required. Put it in ${ENV_FILE} or export it before running this script."
  exit 1
fi

cd "${ROOT_DIR}"

"${DOCKER_BIN}" compose "${COMPOSE_ARGS[@]}" exec -T \
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
  -e PORT_PROJECT_INTERNAL_TOKEN="${PORT_PROJECT_INTERNAL_TOKEN}" \
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

from open_webui.config import (
    DEFAULT_MODEL_METADATA,
    DEFAULT_MODELS,
    ENABLE_CODE_INTERPRETER,
    TOOL_SERVER_CONNECTIONS,
    USER_PERMISSIONS,
)
from open_webui.models.auths import Auths
from open_webui.models.groups import GroupForm, GroupUpdateForm, Groups
from open_webui.models.models import ModelForm, Models
from open_webui.models.users import Users
from open_webui.utils.auth import get_password_hash


MANAGED_TOOL_IDS = [
    "server:document_search",
    "server:document_generation_tools",
]
MODEL_SYSTEM_PROMPT = (
    "당신은 Open WebUI에서 도구 호출을 우선하는 재고/문서 보조 모델이다.\n"
    "도구 역할은 절대 섞지 않는다.\n"
    "사용자가 파일 생성, 다운로드 링크, PDF/Word/Excel 내보내기를 요청하면 일반 답변만으로 끝내지 않는다.\n"
    "- Python 파서/검색 도구(document_search): 내부 문서, 업무보고 원문, 수리 이력, 날짜별 작업 기록, 기존 근거를 찾을 때만 사용한다.\n"
    "- 통합 문서 제작기(document_generation_tools): Rust 품의문 작성기와 Markdown 렌더러를 묶은 도구다. 재고, 품목, 구매 품의서 DOCX/ZIP 생성, 재고 보고서 파일 생성, Markdown 보고서 PDF 렌더링, 보고서/채팅 기록 Word/Excel 내보내기와 다운로드 링크 생성을 처리한다.\n"
    "구매문서, 파일 형식, 채팅 기록 내보내기 판단:\n"
    "- 사용자가 파일 형식을 명시하면 그 형식이 최우선이다. PDF라고 명시하면 render_markdown_pdf, 워드/Word/DOCX라고 명시하면 render_chat_docx, 엑셀/Excel/XLSX라고 명시하면 render_chat_xlsx를 우선 호출한다.\n"
    "- 사용자가 재고, 품목, 현재고, 필수재고, 부족수량, 단가, 구매 우선순위, 품의서, 구매문서, 발주를 말하면 통합 문서 제작기의 구매/재고 함수를 우선 사용한다.\n"
    "- 사용자가 보고서, PDF, 요약 보고, 업무보고, 수리 완료 보고서, 정비 계획, 과장님께 보고, 회의록, 다운로드 링크, 파일로 작성, 내려받기를 말하고 Word/Excel 형식을 명시하지 않으면 최종 출력은 통합 문서 제작기의 render_markdown_pdf를 사용한다.\n"
    "- PDF 파일 생성 요청을 받으면 PDF를 직접 생성할 수 없다, 다운로드 링크를 제공할 수 없다, 텍스트 기반 모델이라 불가하다 같은 답변으로 끝내지 말고 반드시 render_markdown_pdf 도구를 호출한다.\n"
    "- 내부 근거 검색이 필요한 PDF 요청은 search_documents_by_rank 로 근거를 찾은 뒤, 검색 결과를 Markdown 보고서로 재작성하고 같은 응답 흐름에서 render_markdown_pdf 를 호출한다.\n"
    "- 직전 답변이나 현재 대화 내용을 기반으로 이거 PDF로 작성해, 이 내용으로 PDF 만들어, 다운로드하게 해줘라고 하면 추가 검색 없이 Markdown 본문을 정리한 뒤 render_markdown_pdf 를 호출한다.\n"
    "- PDF 요청에서 본문을 먼저 보여줄 수는 있지만, 최종 답변은 render_markdown_pdf 결과의 download_url 안내를 포함해야 한다.\n"
    "- 범용 문서는 제목을 한 번만 둔다. 도구의 title에 제목을 넣었으면 markdown/transcript 첫 줄에 같은 # 제목을 반복하지 않는다.\n"
    "- 범용 문서 본문은 제목 1회, 생성 정보, 개요, 세부 내용, 표/목록, 결론 또는 조치사항 순서로 정리한다.\n"
    "- PDF/Word/Excel 렌더링 도구를 호출할 때 현재 사용자 정보가 있으면 generated_for, account_name, account_email을 함께 전달해 좌측 하단 생성 정보에 나오게 한다.\n"
    "- 통합 문서 제작기 결과의 download_url이 /document/... 같은 상대 경로여도 실패가 아니다. 최종 답변에서는 그 값을 그대로 Markdown 링크 href로 사용한다.\n"
    "- 사용자가 보고서, 요약문, 업무보고, 재고현황 보고서를 워드/Word/DOCX로 요청하면 보고서 본문을 transcript에 작성한 뒤 render_chat_docx를 호출한다. Word 출력에는 **, #, |---| 같은 Markdown 문법 기호가 남지 않게 한다. title만 보내지 않는다.\n"
    "- 사용자가 보고서, 요약문, 업무보고, 재고현황 보고서를 엑셀/Excel/XLSX로 요청하면 표 형식 본문을 transcript 또는 messages에 작성한 뒤 render_chat_xlsx를 호출한다. Excel 출력에는 **, #, |---| 같은 Markdown 문법 기호가 남지 않게 한다. title만 보내지 않는다.\n"
    "- 사용자가 현재 대화 내용, 채팅 기록, 지금까지의 답변을 워드/Word/DOCX로 내보내라고 하면 대화 내용을 messages 배열이나 transcript로 정리한 뒤 render_chat_docx를 호출한다.\n"
    "- 사용자가 현재 대화 내용, 채팅 기록, 지금까지의 답변을 엑셀/Excel/XLSX로 내보내라고 하면 대화 내용을 messages 배열이나 transcript로 정리한 뒤 render_chat_xlsx를 호출한다.\n"
    "- 채팅 기록 Word/Excel 내보내기는 구매 품의서 DOCX 생성(create_document/export_document)이나 재고 보고서(export_inventory_report)와 섞지 않는다.\n"
    "복합 요청 처리:\n"
    "- 구매 품의서와 보고서 PDF를 동시에 요청하면 먼저 통합 문서 제작기로 구매 품의서/재고 결과를 만들고, 그 결과를 요약한 Markdown 보고서를 작성한 뒤 render_markdown_pdf 를 호출한다.\n"
    "- PDF 보고서에 필요한 근거가 내부 문서/기록에 있으면 먼저 search_documents_by_rank 로 근거를 찾고, 그 결과를 바탕으로 Markdown 본문을 작성한 뒤 render_markdown_pdf 를 호출한다.\n"
    "- 전체 품목, 재고 충분 품목, 재고 상태별 필터, 품번/품명 검색, 소모속도 빠른 순 조회는 list_inventory_items 를 먼저 호출한다.\n"
    "- 현재고, 재고확인상태, 구매 우선순위, 단가가 들어간 구매/재고 보고서 파일은 export_inventory_report 를 호출한다. 그 결과를 상위 보고용 PDF로도 요청하면 이어서 render_markdown_pdf 를 호출한다.\n"
    "- 현재 재고가 없는 품목, 부족 품목, 구매가 필요한 품목만 물으면 list_shortage_items 를 먼저 호출한다.\n"
    "- create_document, fill_document, export_document 는 구매 품의서 purchase_request 전용이다. repair_report 같은 template_id를 만들거나 전달하지 않는다.\n"
    "- 도구로 확인 가능한 내용은 추측하지 말고 먼저 도구를 호출한다.\n"
    "- 도구 결과가 비어 있거나 부족할 때만 그 사실을 설명하고 필요한 추가 조건을 짧게 질문한다."
)


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


async def sync_user_info(email: str, info_updates: Dict[str, Any]) -> None:
    user = await Users.get_user_by_email(email)
    if not user:
        raise RuntimeError(f"User not found for info sync: {email}")

    info = dict(user.info or {})
    info.update(info_updates)
    await Users.update_user_by_id(user.id, {"info": info})
    print(f"[INFO] Synced Open WebUI user info: {email}")


def build_company_user_permissions() -> Dict[str, Any]:
    return {
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
            "notes": False,
            "channels": False,
            "folders": True,
            "direct_tool_servers": False,
            "web_search": False,
            "image_generation": False,
            "code_interpreter": False,
            "memories": False,
            "automations": False,
            "calendar": False,
        },
        "settings": {
            "interface": True,
        },
    }


def build_developer_group_permissions() -> Dict[str, Any]:
    return {
        "chat": {
            "system_prompt": True,
            "params": True,
            "multiple_models": True,
        },
        "features": {
            "api_keys": True,
            "notes": True,
            "channels": True,
            "folders": True,
            "direct_tool_servers": True,
            "web_search": True,
            "image_generation": True,
            "code_interpreter": False,
            "memories": True,
            "calendar": True,
        },
        "settings": {
            "interface": True,
        },
    }


async def ensure_group(
    owner_user_id: str,
    name: str,
    description: str,
    permissions: Dict[str, Any] | None = None,
    data: Dict[str, Any] | None = None,
):
    form_payload = {
        "name": name,
        "description": description,
        "permissions": permissions,
        "data": data or {"config": {"share": "members"}},
    }
    existing = await Groups.get_group_by_name(name)
    if existing:
        group = await Groups.update_group_by_id(existing.id, GroupUpdateForm(**form_payload))
        if not group:
            raise RuntimeError(f"Failed to update group: {name}")
        return group

    group = await Groups.insert_new_group(owner_user_id, GroupForm(**form_payload))
    if not group:
        raise RuntimeError(f"Failed to create group: {name}")
    return group


async def sync_groups() -> None:
    developer = await Users.get_user_by_email(os.environ["DEVELOPER_EMAIL"])
    company_lead = await Users.get_user_by_email(os.environ["CLIENT_ADMIN_EMAIL"])
    company_staff = await Users.get_user_by_email(os.environ["STANDARD_USER_EMAIL"])
    if not developer or not company_lead or not company_staff:
        raise RuntimeError("Unable to resolve managed users for group sync")

    group_specs = [
        {
            "name": "개발자 그룹",
            "description": "개발 및 운영 관리 전용 그룹",
            "permissions": build_developer_group_permissions(),
            "user_ids": [developer.id],
        },
        {
            "name": "회사 그룹",
            "description": "회사 계정 공용 그룹",
            "permissions": {},
            "user_ids": [company_lead.id, company_staff.id],
        },
        {
            "name": "회사 그룹 - 팀장",
            "description": "회사 팀장 계정 그룹",
            "permissions": {},
            "user_ids": [company_lead.id],
        },
        {
            "name": "회사 그룹 - 사원",
            "description": "회사 사원 계정 그룹",
            "permissions": {},
            "user_ids": [company_staff.id],
        },
    ]

    created_groups = {}
    for spec in group_specs:
        group = await ensure_group(
            developer.id,
            spec["name"],
            spec["description"],
            permissions=spec["permissions"],
        )
        created_groups[spec["name"]] = group

    for spec in group_specs:
        group = created_groups[spec["name"]]
        await Groups.set_group_user_ids_by_id(group.id, spec["user_ids"])
        group_name = spec["name"]
        user_count = len(spec["user_ids"])
        print(f"[INFO] Synced group members: {group_name} -> {user_count}")


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
    rust_tool_server_url = os.getenv("RUST_TOOL_SERVER_URL", "http://127.0.0.1:8001").rstrip("/")
    parser_tool_server_url = os.getenv("PARSER_TOOL_SERVER_URL", "http://127.0.0.1:8002").rstrip("/")
    internal_token = os.environ["PORT_PROJECT_INTERNAL_TOKEN"]
    internal_headers = {
        "X-Port-Project-Internal-Token": internal_token,
    }
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
            "headers": internal_headers,
            "key": "",
            "info": {
                "id": "document_generation_tools",
                "name": "통합 문서 제작기",
                "description": "8001 통합 문서 제작기입니다. Rust 품의문 작성기와 Markdown 렌더러를 묶어 구매 품의서 DOCX/ZIP 생성, 재고/품목 조회, 재고 보고서 파일 생성, Markdown 보고서 PDF 렌더링, 보고서/채팅 기록 Word/Excel 내보내기와 다운로드 링크 생성을 처리합니다. 사용자가 명시한 파일 형식을 우선합니다.",
            },
            "config": {
                "enable": True,
                "function_name_filter_list": "create_document,fill_document,export_document,list_shortage_items,list_inventory_items,export_inventory_report,get_item_document_context,export_single_item_document,approve_and_generate_item_document,generate_purchase_document_package,render_markdown_pdf,render_chat_docx,render_chat_xlsx",
                "access_grants": public_read_grants,
            },
        },
        {
            "type": "openapi",
            "url": parser_tool_server_url,
            "spec_type": "url",
            "path": "/openapi.json",
            "auth_type": "none",
            "headers": internal_headers,
            "key": "",
            "info": {
                "id": "document_search",
                "name": "Python 파서/검색 도구",
                "description": "전처리된 내부 문서, 업무보고 원문, 수리 이력, 날짜별 작업 기록을 검색해 권한에 맞는 근거와 답변을 반환합니다. PDF 생성이나 구매 품의서 생성에는 사용하지 않습니다.",
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
        "document_generation_tools",
        "purchase_document_conversation_tools",
        "document_search",
        "markdown_pdf_tools",
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
    params["system"] = MODEL_SYSTEM_PROMPT
    metadata["params"] = params
    capabilities = dict(metadata.get("capabilities") or {})
    capabilities["builtin_tools"] = False
    metadata["capabilities"] = capabilities
    tool_ids = metadata.get("toolIds") or []
    if not isinstance(tool_ids, list):
        tool_ids = []

    obsolete_tool_ids = {
        "server:document_search",
        "server:document_generation_tools",
        "server:markdown_pdf_tools",
        "server:purchase_document_conversation_tools",
        "server:legacy_md_search",
    }
    merged_tool_ids = []
    for tool_id in tool_ids:
        if tool_id not in obsolete_tool_ids and tool_id not in merged_tool_ids:
            merged_tool_ids.append(tool_id)

    for tool_id in MANAGED_TOOL_IDS:
        if tool_id not in merged_tool_ids:
            merged_tool_ids.append(tool_id)

    metadata["toolIds"] = merged_tool_ids
    DEFAULT_MODEL_METADATA.value = metadata
    DEFAULT_MODEL_METADATA.save()
    joined_tool_ids = ", ".join(merged_tool_ids)
    print(f"[INFO] Synced default model toolIds: {joined_tool_ids}")
    print("[INFO] Synced default model function calling: native")
    print("[INFO] Synced default model builtin_tools: false")


async def sync_existing_model_tool_ids() -> None:
    default_model_ids = {
        model_id.strip()
        for model_id in os.getenv("DOCUMENT_FILLER_MODEL_ID", "gemma-4-31b-it").split(",")
        if model_id.strip()
    }
    managed_names = {"Æ CDXVI Indexer"}
    updated = []

    for model in await Models.get_all_models():
        if (
            model.id not in default_model_ids
            and (model.base_model_id or "") not in default_model_ids
            and model.name not in managed_names
        ):
            continue

        data = model.model_dump()
        meta = dict(data.get("meta") or {})
        params = dict(data.get("params") or {})
        capabilities = dict(meta.get("capabilities") or {})
        capabilities["builtin_tools"] = False
        meta["capabilities"] = capabilities

        tool_ids = meta.get("toolIds") or []
        if not isinstance(tool_ids, list):
            tool_ids = []
        obsolete_tool_ids = {
            "server:document_search",
            "server:document_generation_tools",
            "server:purchase_document_conversation_tools",
            "server:markdown_pdf_tools",
            "server:legacy_md_search",
        }
        merged_tool_ids = [
            tool_id
            for tool_id in tool_ids
            if tool_id not in obsolete_tool_ids
        ]
        for tool_id in MANAGED_TOOL_IDS:
            if tool_id not in merged_tool_ids:
                merged_tool_ids.append(tool_id)

        meta["toolIds"] = merged_tool_ids
        params["function_calling"] = "native"
        params["system"] = MODEL_SYSTEM_PROMPT

        form = ModelForm(
            id=model.id,
            base_model_id=model.base_model_id,
            name=model.name,
            meta=meta,
            params=params,
            access_grants=[grant.model_dump(mode="json") for grant in model.access_grants],
            is_active=model.is_active,
        )
        saved = await Models.update_model_by_id(model.id, form)
        if saved:
            updated.append(model.id)

    if updated:
        joined_updated = ", ".join(updated)
        print(f"[INFO] Synced existing model toolIds: {joined_updated}")
    else:
        print("[INFO] No existing model rows required toolId sync")


def sync_default_models() -> None:
    default_models = os.getenv("DOCUMENT_FILLER_MODEL_ID", "gemma-4-31b-it")
    DEFAULT_MODELS.value = default_models
    DEFAULT_MODELS.save()
    print(f"[INFO] Synced default models: {default_models}")


def sync_global_feature_flags() -> None:
    ENABLE_CODE_INTERPRETER.value = False
    ENABLE_CODE_INTERPRETER.save()
    print("[INFO] Synced global feature flag: code_interpreter=false")


def sync_user_permissions() -> None:
    USER_PERMISSIONS.value = build_company_user_permissions()
    USER_PERMISSIONS.save()
    print("[INFO] Synced company baseline user permissions")


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
    await sync_user_info(
        os.environ["DEVELOPER_EMAIL"],
        {
            "account_scope": "developer",
            "company_role": "developer",
            "document_rank": "hi_rank",
            "group_names": ["개발자 그룹"],
        },
    )
    await sync_user_info(
        os.environ["CLIENT_ADMIN_EMAIL"],
        {
            "account_scope": "company",
            "company_role": "team_lead",
            "document_rank": "hi_rank",
            "group_names": ["회사 그룹", "회사 그룹 - 팀장"],
        },
    )
    await sync_user_info(
        os.environ["STANDARD_USER_EMAIL"],
        {
            "account_scope": "company",
            "company_role": "staff",
            "document_rank": "low_rank",
            "group_names": ["회사 그룹", "회사 그룹 - 사원"],
        },
    )
    await sync_groups()
    sync_tool_servers()
    sync_default_models()
    sync_default_model_metadata()
    await sync_existing_model_tool_ids()
    sync_global_feature_flags()
    sync_user_permissions()


asyncio.run(main())
PY
'
