from __future__ import annotations

import json
import os
import re
import secrets
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field


app = FastAPI(title="suno-service", version="0.1.0", openapi_url=None)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


PUBLIC_BASE_URL = os.getenv("SUNO_SERVICE_PUBLIC_BASE_URL", "http://127.0.0.1:8005").rstrip("/")
INTERNAL_TOKEN_HEADER = "x-port-project-internal-token"
OPEN_WEBUI_USER_EMAIL_HEADER = "x-openwebui-user-email"
OPEN_WEBUI_USER_ID_HEADER = "x-openwebui-user-id"

DEFAULT_LLM_API_URL = "http://192.168.100.13:8000/v1/chat/completions"
DEFAULT_LLM_MODEL_ID = "gemma-4-31b-it"

SUNO_TEMPLATE_DIR = Path(os.getenv("SUNO_TEMPLATE_DIR", "/app/templates"))
SUNO_GUIDE_FILENAME = os.getenv("SUNO_GUIDE_FILENAME", "suno_guide.md")
SUNO_GUIDE_HOT_RELOAD = os.getenv("SUNO_GUIDE_HOT_RELOAD", "false").lower() == "true"


# ----- auth ---------------------------------------------------------------

def configured_internal_token() -> str:
    token = os.getenv("PORT_PROJECT_INTERNAL_TOKEN", "").strip()
    if not token:
        raise HTTPException(status_code=500, detail="PORT_PROJECT_INTERNAL_TOKEN is not configured")
    return token


def require_internal_request(raw_request: Request) -> None:
    expected = configured_internal_token()
    supplied = (raw_request.headers.get(INTERNAL_TOKEN_HEADER) or "").strip()
    if not secrets.compare_digest(supplied, expected):
        raise HTTPException(status_code=403, detail="invalid internal tool token")


def require_registered_tool_user(raw_request: Request) -> dict[str, str]:
    require_internal_request(raw_request)
    email = (raw_request.headers.get(OPEN_WEBUI_USER_EMAIL_HEADER) or "").strip().lower()
    user_id = (raw_request.headers.get(OPEN_WEBUI_USER_ID_HEADER) or "").strip()
    if not email or not user_id:
        raise HTTPException(status_code=401, detail="registered Open WebUI account is required")
    return {"email": email, "user_id": user_id}


# ----- LLM call helpers (mirrors python-service pattern) ------------------

def call_chat_completions(api_url: str, payload: dict[str, Any]) -> str:
    request = urllib.request.Request(
        api_url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            return response.read().decode("utf-8")
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise HTTPException(status_code=502, detail=f"LLM 호출 실패: HTTP {error.code} {detail}") from error
    except urllib.error.URLError as error:
        raise HTTPException(status_code=502, detail=f"LLM 호출 실패: {error.reason}") from error


def extract_message_content(raw_response: str) -> str:
    try:
        data = json.loads(raw_response)
    except json.JSONDecodeError:
        return raw_response.strip()
    choices = data.get("choices") or []
    if not choices:
        return raw_response.strip()
    message = choices[0].get("message") or {}
    content = message.get("content")
    if isinstance(content, str):
        return content.strip()
    return raw_response.strip()


def llm_generate(messages: list[dict[str, str]], temperature: float = 0.85) -> str:
    api_url = os.getenv("SUNO_LLM_API_URL", DEFAULT_LLM_API_URL)
    model = os.getenv("SUNO_LLM_MODEL_ID", DEFAULT_LLM_MODEL_ID)
    payload = {
        "model": model,
        "messages": messages,
        "stream": False,
        "temperature": temperature,
    }
    raw = call_chat_completions(api_url=api_url, payload=payload)
    return extract_message_content(raw)


def strip_code_fences(text: str) -> str:
    """LLM이 가끔 ```로 감싸 보내는 경우를 정리."""
    cleaned = text.strip()
    fence_match = re.match(r"^```(?:\w+)?\s*\n(.*?)\n```\s*$", cleaned, flags=re.DOTALL)
    if fence_match:
        return fence_match.group(1).strip()
    return cleaned


# ----- Suno guide knowledge (loaded from external templates dir) ----------

_GUIDE_CACHE: str | None = None


def _load_suno_guide() -> str:
    guide_path = SUNO_TEMPLATE_DIR / SUNO_GUIDE_FILENAME
    if not guide_path.is_file():
        raise HTTPException(
            status_code=500,
            detail=f"Suno guide template not found: {guide_path}. "
            f"Set SUNO_TEMPLATE_DIR or place {SUNO_GUIDE_FILENAME} under /app/templates.",
        )
    return guide_path.read_text(encoding="utf-8")


def get_suno_guide() -> str:
    """가이드 본문을 시스템 프롬프트로 반환. SUNO_GUIDE_HOT_RELOAD=true면 매 호출마다 디스크에서 다시 읽음."""
    global _GUIDE_CACHE
    if SUNO_GUIDE_HOT_RELOAD or _GUIDE_CACHE is None:
        _GUIDE_CACHE = _load_suno_guide()
    return _GUIDE_CACHE


def language_directive(language: str) -> str:
    table = {
        "ko": "가사는 한국어로 작성합니다.",
        "en": "Write the lyrics in English.",
        "ja": "歌詞は日本語で書きます。",
        "mixed": "Verse는 한국어, Chorus와 Hook은 영어로 자연스럽게 섞어 작성합니다.",
    }
    return table.get(language, table["ko"])


# ----- deterministic validators -------------------------------------------

KNOWN_STRUCTURE_TAGS = {
    "[Intro]", "[Verse]", "[Verse 1]", "[Verse 2]", "[Verse 3]",
    "[Pre-Chorus]", "[Chorus]", "[Final Chorus]", "[Bridge]", "[Outro]",
    "[Hook]", "[Refrain]", "[Interlude]", "[Break]",
    "[Build-Up]", "[Breakdown]", "[Big Finish]",
    "[Instrumental]", "[Guitar Solo]", "[Sax Solo]", "[Percussion Break]", "[Bass Drop]",
}

GENRE_BPM_RANGES: list[tuple[str, tuple[int, int]]] = [
    ("trap", (70, 90)),
    ("hip-hop", (70, 90)),
    ("hip hop", (70, 90)),
    ("lo-fi", (70, 90)),
    ("lofi", (70, 90)),
    ("ballad", (60, 80)),
    ("r&b", (80, 100)),
    ("rnb", (80, 100)),
    ("neo-soul", (80, 100)),
    ("k-pop", (100, 130)),
    ("kpop", (100, 130)),
    ("j-pop", (100, 130)),
    ("synth-pop", (100, 130)),
    ("pop", (100, 130)),
    ("rock", (100, 140)),
    ("indie", (100, 140)),
    ("metal", (140, 180)),
    ("tech house", (120, 128)),
    ("deep house", (120, 128)),
    ("house", (120, 128)),
    ("trance", (130, 142)),
    ("drum and bass", (170, 180)),
    ("dnb", (170, 180)),
    ("hardcore", (180, 220)),
]


def char_limit_for(target_model: str) -> int:
    return 200 if target_model == "v4" else 1000


def find_structure_tags(text: str) -> list[str]:
    return re.findall(r"\[[A-Za-z][A-Za-z0-9 \-]{0,40}\]", text)


def detect_bpm(text: str) -> int | None:
    match = re.search(r"(\d{2,3})\s*BPM", text, flags=re.IGNORECASE)
    if not match:
        return None
    bpm = int(match.group(1))
    return bpm if 40 <= bpm <= 260 else None


def bpm_in_genre_range(text: str, bpm: int) -> bool | None:
    """text(스타일 프롬프트)에서 장르 키워드를 찾아 BPM이 범위 안인지 검사.
    매칭되는 장르가 없으면 None(판단 보류)."""
    haystack = text.lower()
    for keyword, (low, high) in GENRE_BPM_RANGES:
        if keyword in haystack:
            return low <= bpm <= high
    return None


def validate_lyrics(text: str) -> tuple[dict[str, Any], list[str]]:
    tags = find_structure_tags(text)
    unknown = [t for t in tags if t not in KNOWN_STRUCTURE_TAGS]
    warnings: list[str] = []
    if unknown:
        joined = ", ".join(sorted(set(unknown)))
        warnings.append(
            f"Suno가 인식하지 않을 가능성이 높은 구조 태그 사용: {joined}. "
            "안정 태그([Verse]/[Chorus]/[Bridge] 등)로 교체를 권장합니다."
        )
    if "[Chorus]" not in tags and "[Hook]" not in tags and "[Refrain]" not in tags:
        warnings.append("코러스/훅 태그가 없습니다. Suno가 후렴 위치를 잡지 못할 수 있습니다.")

    char_count = len(text)
    if char_count > 3000:
        warnings.append(f"가사 길이가 {char_count}자로 권장 한도(약 3,000자)를 초과했습니다.")
    return (
        {
            "char_count": char_count,
            "char_limit": 3000,
            "structure_tags_used": tags,
        },
        warnings,
    )


def validate_style(text: str, target_model: str) -> tuple[dict[str, Any], list[str]]:
    limit = char_limit_for(target_model)
    char_count = len(text)
    warnings: list[str] = []
    if char_count > limit:
        warnings.append(
            f"스타일 프롬프트가 {char_count}자로 {target_model.upper()} 한도({limit}자)를 초과했습니다. "
            "초과분은 Suno에서 경고 없이 잘립니다."
        )
    bpm = detect_bpm(text)
    metadata: dict[str, Any] = {
        "char_count": char_count,
        "char_limit": limit,
        "bpm_detected": bpm,
    }
    if bpm is not None:
        in_range = bpm_in_genre_range(text, bpm)
        metadata["bpm_in_genre_range"] = in_range
        if in_range is False:
            warnings.append(
                f"감지된 BPM {bpm}이 명시된 장르의 표준 범위와 어긋날 수 있습니다."
            )
    else:
        metadata["bpm_in_genre_range"] = None
        warnings.append("BPM이 감지되지 않았습니다. 5-Part Formula의 5번 슬롯이 비어 있을 수 있습니다.")
    return metadata, warnings


# ----- request / response models ------------------------------------------

LanguageEnum = Literal["ko", "en", "ja", "mixed"]
ModelEnum = Literal["v4", "v5"]
StyleFormatEnum = Literal["tag_list", "natural"]


class GenerateLyricsRequest(BaseModel):
    topic: str = Field(..., description="곡이 말하려는 주제·장면·감정")
    language: LanguageEnum = Field(..., description="가사 언어. mixed는 사용자가 원할 때만")
    description: str | None = Field(None, description="장르·무드·보컬·구조·길이 등 자유 서술")
    key_phrases: list[str] | None = Field(None, description="가사에 반드시 포함할 라인·단어")
    current_style_prompt: str | None = Field(None, description="이미 있는 스타일 프롬프트(톤 정합용)")


class ImproveLyricsRequest(BaseModel):
    current_lyrics: str = Field(..., description="기존 가사(구조 태그 포함 그대로)")
    feedback: str = Field(..., description="자유 서술 개선 방향")
    preserve: str | None = Field(None, description="자유 서술 보존 지시")
    current_style_prompt: str | None = Field(None, description="스타일 프롬프트 톤 정합용")


class GenerateStylePromptRequest(BaseModel):
    description: str = Field(..., description="원하는 곡의 자유 서술")
    target_model: ModelEnum = Field("v5", description="글자수 한도 결정")
    format: StyleFormatEnum = Field("tag_list", description="콤마 태그 또는 V5 자연어")
    exclude: str | None = Field(None, description="자유 서술 네거티브")
    current_lyrics: str | None = Field(None, description="기존 가사 톤 정합용")


class ImproveStylePromptRequest(BaseModel):
    current_prompt: str = Field(..., description="기존 스타일 프롬프트")
    feedback: str = Field(..., description="자유 서술 개선 방향")
    target_model: ModelEnum = Field("v5", description="글자수 한도 변경 시")
    current_lyrics: str | None = Field(None, description="가사 톤 정합용")


class SunoResponse(BaseModel):
    result: str
    metadata: dict[str, Any]
    warnings: list[str] = []
    suggestions: list[str] = []


# ----- prompt builders ----------------------------------------------------

def build_generate_lyrics_messages(req: GenerateLyricsRequest) -> list[dict[str, str]]:
    user_lines = [
        "다음 조건으로 Suno V5용 가사를 작성하세요.",
        f"- 주제: {req.topic}",
        f"- {language_directive(req.language)}",
    ]
    if req.description:
        user_lines.append(f"- 곡 분위기/스타일 지시: {req.description}")
    if req.key_phrases:
        joined = " / ".join(req.key_phrases)
        user_lines.append(f"- 가사에 반드시 포함할 라인·단어: {joined}")
    if req.current_style_prompt:
        user_lines.append(
            f"- 함께 사용할 스타일 프롬프트(톤을 맞추되 가사에는 같은 디스크립터를 베끼지 말 것):\n{req.current_style_prompt}"
        )
    user_lines.append(
        "구조 태그([Verse]/[Chorus]/[Bridge] 등)와 필요한 인라인 보컬 큐를 포함해 본문만 출력하세요."
    )
    return [
        {"role": "system", "content": get_suno_guide()},
        {"role": "user", "content": "\n".join(user_lines)},
    ]


def build_improve_lyrics_messages(req: ImproveLyricsRequest) -> list[dict[str, str]]:
    user_lines = [
        "다음 가사를 개선하세요. 구조 태그를 유지하되, 피드백 방향에 맞게 라인을 다시 씁니다.",
        f"[기존 가사]\n{req.current_lyrics}",
        f"[개선 방향]\n{req.feedback}",
    ]
    if req.preserve:
        user_lines.append(f"[보존 지시]\n{req.preserve}")
    if req.current_style_prompt:
        user_lines.append(f"[참고 스타일 프롬프트]\n{req.current_style_prompt}")
    user_lines.append("개선된 가사 전체를 본문만 출력하세요. 설명을 덧붙이지 마세요.")
    return [
        {"role": "system", "content": get_suno_guide()},
        {"role": "user", "content": "\n".join(user_lines)},
    ]


def build_generate_style_messages(req: GenerateStylePromptRequest) -> list[dict[str, str]]:
    limit = char_limit_for(req.target_model)
    fmt_directive = (
        "콤마로 구분된 태그 리스트 한 덩어리"
        if req.format == "tag_list"
        else "자연어 대화체 한 문단(핵심 디스크립터를 시작과 끝에 모두 배치)"
    )
    user_lines = [
        f"다음 곡 설명에 맞춰 Suno {req.target_model.upper()} 스타일 프롬프트를 {fmt_directive}로 작성하세요.",
        f"- 글자수 한도: {limit}자 이내",
        "- 5-Part Formula(장르 / 무드 / 보컬 / 악기·프로덕션 / BPM)를 모두 채울 것",
        f"[곡 설명]\n{req.description}",
    ]
    if req.exclude:
        user_lines.append(f"[제외할 요소]\n{req.exclude}")
    if req.current_lyrics:
        user_lines.append(
            f"[함께 사용할 가사(톤 참고용, 디스크립터 중복 금지)]\n{req.current_lyrics}"
        )
    user_lines.append("결과 본문만 출력하세요. 설명·머리말 금지.")
    return [
        {"role": "system", "content": get_suno_guide()},
        {"role": "user", "content": "\n".join(user_lines)},
    ]


def build_improve_style_messages(req: ImproveStylePromptRequest) -> list[dict[str, str]]:
    limit = char_limit_for(req.target_model)
    user_lines = [
        f"다음 Suno 스타일 프롬프트를 개선하세요. 결과는 {limit}자 이내, 5-Part Formula를 유지합니다.",
        f"[기존 프롬프트]\n{req.current_prompt}",
        f"[개선 방향]\n{req.feedback}",
    ]
    if req.current_lyrics:
        user_lines.append(f"[참고 가사(톤 정합용)]\n{req.current_lyrics}")
    user_lines.append("개선된 프롬프트 본문만 출력하세요.")
    return [
        {"role": "system", "content": get_suno_guide()},
        {"role": "user", "content": "\n".join(user_lines)},
    ]


# ----- endpoints ----------------------------------------------------------

@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/suno/lyrics/generate", response_model=SunoResponse)
def generate_lyrics(req: GenerateLyricsRequest, raw_request: Request) -> SunoResponse:
    require_registered_tool_user(raw_request)
    raw = llm_generate(build_generate_lyrics_messages(req), temperature=0.9)
    result = strip_code_fences(raw)
    metadata, warnings = validate_lyrics(result)
    suggestions: list[str] = []
    if "[Bridge]" not in metadata["structure_tags_used"]:
        suggestions.append("Bridge 추가로 다이내믹을 한 번 더 흔들어 볼 수 있습니다.")
    if "[Pre-Chorus]" not in metadata["structure_tags_used"]:
        suggestions.append("Pre-Chorus를 넣으면 Chorus 진입 텐션이 강해집니다.")
    return SunoResponse(result=result, metadata=metadata, warnings=warnings, suggestions=suggestions)


@app.post("/suno/lyrics/improve", response_model=SunoResponse)
def improve_lyrics(req: ImproveLyricsRequest, raw_request: Request) -> SunoResponse:
    require_registered_tool_user(raw_request)
    raw = llm_generate(build_improve_lyrics_messages(req), temperature=0.85)
    result = strip_code_fences(raw)
    metadata, warnings = validate_lyrics(result)
    return SunoResponse(result=result, metadata=metadata, warnings=warnings, suggestions=[])


@app.post("/suno/style/generate", response_model=SunoResponse)
def generate_style_prompt(req: GenerateStylePromptRequest, raw_request: Request) -> SunoResponse:
    require_registered_tool_user(raw_request)
    raw = llm_generate(build_generate_style_messages(req), temperature=0.8)
    result = strip_code_fences(raw)
    metadata, warnings = validate_style(result, req.target_model)
    suggestions: list[str] = []
    if metadata["char_count"] < metadata["char_limit"] * 0.4:
        suggestions.append(
            "디스크립터가 적습니다. 보컬 카테고리(timbre/breathiness/emotion)를 1-2개 더 보강할 여지가 있습니다."
        )
    return SunoResponse(result=result, metadata=metadata, warnings=warnings, suggestions=suggestions)


@app.post("/suno/style/improve", response_model=SunoResponse)
def improve_style_prompt(req: ImproveStylePromptRequest, raw_request: Request) -> SunoResponse:
    require_registered_tool_user(raw_request)
    raw = llm_generate(build_improve_style_messages(req), temperature=0.8)
    result = strip_code_fences(raw)
    metadata, warnings = validate_style(result, req.target_model)
    return SunoResponse(result=result, metadata=metadata, warnings=warnings, suggestions=[])


# ----- hand-rolled OpenAPI ------------------------------------------------

_SUNO_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "result": {"type": "string", "description": "생성/개선된 가사 또는 스타일 프롬프트 본문"},
        "metadata": {"type": "object", "description": "글자수, 구조 태그, BPM 감지 등 결정론 검사 결과"},
        "warnings": {"type": "array", "items": {"type": "string"}, "description": "Suno 가이드 위반 자동 감지"},
        "suggestions": {"type": "array", "items": {"type": "string"}, "description": "다음 개선 후크"},
    },
}


@app.get("/openapi.json")
def openapi_spec() -> dict[str, Any]:
    return {
        "openapi": "3.0.0",
        "info": {
            "title": "Suno Lyrics & Style Prompt Tools",
            "version": "0.1.0",
            "description": (
                "Suno V5/V5.5용 노래 가사 작성·개선과 스타일 프롬프트 작성·개선을 수행하는 도구. "
                "사용자가 곡, 가사, Suno, 작사, 스타일 프롬프트, 음악 생성 프롬프트를 말하면 호출한다. "
                "내부 사내 문서 검색이나 일반 보고서 PDF 생성에는 사용하지 않는다."
            ),
        },
        "servers": [{"url": PUBLIC_BASE_URL}],
        "paths": {
            "/health": {
                "get": {
                    "operationId": "suno_health_check",
                    "summary": "Health check",
                    "responses": {"200": {"description": "Healthy"}},
                }
            },
            "/suno/lyrics/generate": {
                "post": {
                    "operationId": "generate_suno_lyrics",
                    "summary": "주제·자유 서술 기반 Suno 가사 생성",
                    "description": (
                        "사용자가 곡 주제·메시지·분위기를 자연어로 주면 Suno V5용 가사를 작성한다. "
                        "구조 태그([Verse]/[Chorus]/[Bridge] 등)와 필요한 인라인 보컬 큐를 포함한다. "
                        "스타일 프롬프트가 따로 필요하면 generate_suno_style_prompt를 별도 호출한다."
                    ),
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["topic", "language"],
                                    "properties": {
                                        "topic": {
                                            "type": "string",
                                            "description": "곡이 말하려는 주제·장면·감정",
                                        },
                                        "language": {
                                            "type": "string",
                                            "enum": ["ko", "en", "ja", "mixed"],
                                            "description": "가사 언어. mixed는 사용자가 직접 원할 때만",
                                        },
                                        "description": {
                                            "type": "string",
                                            "description": "장르·무드·보컬·구조·길이 등 자유 서술",
                                        },
                                        "key_phrases": {
                                            "type": "array",
                                            "items": {"type": "string"},
                                            "description": "가사에 반드시 들어가야 할 라인 또는 단어",
                                        },
                                        "current_style_prompt": {
                                            "type": "string",
                                            "description": "이미 있는 스타일 프롬프트(톤 정합용)",
                                        },
                                    },
                                }
                            }
                        },
                    },
                    "responses": {
                        "200": {
                            "description": "생성된 가사",
                            "content": {"application/json": {"schema": _SUNO_RESPONSE_SCHEMA}},
                        }
                    },
                }
            },
            "/suno/lyrics/improve": {
                "post": {
                    "operationId": "improve_suno_lyrics",
                    "summary": "기존 가사 + 자유 피드백으로 가사 개선",
                    "description": (
                        "기존 Suno 가사와 자연어 개선 방향(예: '코러스가 약하다', '2절을 1절과 차별화')을 받아 "
                        "수정된 가사를 반환한다. 보존 지시(preserve)도 자유 서술로 받는다."
                    ),
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["current_lyrics", "feedback"],
                                    "properties": {
                                        "current_lyrics": {
                                            "type": "string",
                                            "description": "기존 가사(구조 태그 포함 그대로)",
                                        },
                                        "feedback": {
                                            "type": "string",
                                            "description": "자유 서술 개선 방향",
                                        },
                                        "preserve": {
                                            "type": "string",
                                            "description": "자유 서술 보존 지시(예: '후렴은 그대로')",
                                        },
                                        "current_style_prompt": {
                                            "type": "string",
                                            "description": "스타일 프롬프트가 있으면 톤 정합용으로",
                                        },
                                    },
                                }
                            }
                        },
                    },
                    "responses": {
                        "200": {
                            "description": "개선된 가사",
                            "content": {"application/json": {"schema": _SUNO_RESPONSE_SCHEMA}},
                        }
                    },
                }
            },
            "/suno/style/generate": {
                "post": {
                    "operationId": "generate_suno_style_prompt",
                    "summary": "자유 서술 기반 Suno 스타일 프롬프트 생성",
                    "description": (
                        "원하는 곡의 자유 서술을 받아 Suno V4/V5 Style 필드용 프롬프트를 5-Part Formula에 맞춰 작성한다. "
                        "format=tag_list(콤마 태그) 또는 natural(V5 대화체)을 지원한다."
                    ),
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["description"],
                                    "properties": {
                                        "description": {
                                            "type": "string",
                                            "description": "원하는 곡의 자유 서술",
                                        },
                                        "target_model": {
                                            "type": "string",
                                            "enum": ["v4", "v5"],
                                            "default": "v5",
                                            "description": "글자수 한도(V4 200, V5 1000) 결정",
                                        },
                                        "format": {
                                            "type": "string",
                                            "enum": ["tag_list", "natural"],
                                            "default": "tag_list",
                                            "description": "tag_list=콤마 구분 / natural=V5 대화체",
                                        },
                                        "exclude": {
                                            "type": "string",
                                            "description": "자유 서술 네거티브(예: 'autotune, trap drums 빼고')",
                                        },
                                        "current_lyrics": {
                                            "type": "string",
                                            "description": "기존 가사가 있으면 톤 정합용으로",
                                        },
                                    },
                                }
                            }
                        },
                    },
                    "responses": {
                        "200": {
                            "description": "생성된 스타일 프롬프트",
                            "content": {"application/json": {"schema": _SUNO_RESPONSE_SCHEMA}},
                        }
                    },
                }
            },
            "/suno/style/improve": {
                "post": {
                    "operationId": "improve_suno_style_prompt",
                    "summary": "기존 스타일 프롬프트 + 자유 피드백으로 개선",
                    "description": (
                        "기존 Suno 스타일 프롬프트와 자연어 개선 방향(예: 'BPM 빠르게', '보컬 raspy하게')을 받아 "
                        "수정된 프롬프트를 반환한다. target_model로 V4↔V5 한도 마이그레이션도 가능."
                    ),
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["current_prompt", "feedback"],
                                    "properties": {
                                        "current_prompt": {
                                            "type": "string",
                                            "description": "기존 스타일 프롬프트",
                                        },
                                        "feedback": {
                                            "type": "string",
                                            "description": "자유 서술 개선 방향",
                                        },
                                        "target_model": {
                                            "type": "string",
                                            "enum": ["v4", "v5"],
                                            "default": "v5",
                                            "description": "글자수 한도 변경 시 사용",
                                        },
                                        "current_lyrics": {
                                            "type": "string",
                                            "description": "가사가 있으면 톤 정합용으로",
                                        },
                                    },
                                }
                            }
                        },
                    },
                    "responses": {
                        "200": {
                            "description": "개선된 스타일 프롬프트",
                            "content": {"application/json": {"schema": _SUNO_RESPONSE_SCHEMA}},
                        }
                    },
                }
            },
        },
    }
