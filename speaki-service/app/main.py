from __future__ import annotations

import os
import random
import re
import secrets
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field


app = FastAPI(title="speaki-service", version="0.1.0", openapi_url=None)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


PUBLIC_BASE_URL = os.getenv("SPEAKI_SERVICE_PUBLIC_BASE_URL", "http://127.0.0.1:8006").rstrip("/")
INTERNAL_TOKEN_HEADER = "x-port-project-internal-token"
OPEN_WEBUI_USER_EMAIL_HEADER = "x-openwebui-user-email"
OPEN_WEBUI_USER_ID_HEADER = "x-openwebui-user-id"


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


# Speaki canonical dialogue dictionary.
# Each line below is a verbatim Speaki utterance taken from the Speaki lore.
# The service is only allowed to return strings drawn from this dictionary.
SPEAKI_LINES: dict[str, list[str]] = {
    "default": ["스삐끼!", "스피키!", "스삐끼, 스피키!"],
    "surprise": ["스삨!", "스핔!", "스삨, 스핔!"],
    "discover": ["스핔!", "후와아~!", "스삨!"],
    "sleep": ["스핔...", "스삨...", "스핔..."],
    "angry": ["피키!", "피킼! 피킼!", "피키! 피키!"],
    "happy_short": ["쪼와요!"],
    "happy_long_template": ["쪼와요~ 쪼와요~ {what} 쪼와요~"],
    "happy_long_default": [
        "쪼와요~ 쪼와요~ 호박이 쪼와요~",
        "쪼와요~ 쪼와요~ 호박친구 쪼와요~",
        "쪼와요~ 쪼와요~ 물걸레질 쪼와요~",
        "쪼와요~ 쪼와요~ 숨바꼭질 쪼와요~",
    ],
    "cry": ["흐아아!", "흐아악!", "흐에엥!", "으에엥!", "흐으아악!"],
    "wonder": ["후와아~!", "후와아~!"],
    "reverse_cry": ["아~우!", "에~윽!", "에~으!"],
    "question": ["아!?", "에!?", "아, 에!?"],
    "stop_short": ["스피키 네르지 마세요!"],
    "stop_long": [
        "스피키 머리 잡아당기지 마세요! 네르는 이렇게 폭력적인 역할이 아니란 말이에요!",
    ],
    "stop_template": ["스피키 {verb}지 마세요!"],
    "want": ["스피키모!"],
    "plead": ["마세요!", "바세요!", "밧세요!"],
    "hurt": ["이테요!", "히데요!", "이에요!"],
    "tried_hard": ["스피키 열심히 했는데..."],
    "sleep_talk": ["스핔...", "스핔..."],
    "negative_ending": ["는데...", "난데..."],
    "pumpkin": ["호바기!", "호박이!", "호박이 쪼와요~"],
}


# Loose Korean/Japanese keyword routing. Keep simple — no NLP, just substring hints.
INTENT_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    ("angry", ("화", "짜증", "열받", "怒", "むかつく", "ムカ")),
    ("hurt", ("아파", "아프", "아야", "痛", "いた")),
    ("cry", ("울", "슬퍼", "슬프", "눈물", "泣", "悲し", "かなし")),
    ("wonder", ("우와", "와아", "대박", "멋져", "すご", "凄", "わあ")),
    ("question", ("뭐", "왜", "어째", "?", "？", "なに", "何", "どう")),
    ("stop_long", ("그만", "하지마", "하지 마", "멈춰", "やめ", "止め")),
    ("plead", ("살려", "도와", "助け", "たすけ")),
    ("want", ("나도", "주세요", "줘", "갖고싶", "ほしい", "欲し")),
    ("pumpkin", ("호박", "ホバ", "かぼちゃ", "カボチャ", "pumpkin")),
    ("tried_hard", ("열심히", "노력", "頑張", "がんば")),
    ("sleep", ("자", "잠", "졸려", "ねむ", "眠")),
    ("happy_long_default", ("좋아", "조아", "쪼아", "쪼와", "好き", "すき", "love", "like")),
    ("surprise", ("놀라", "헉", "헐", "びっくり", "驚")),
    ("discover", ("찾았", "발견", "見つけ", "みつけ")),
    ("greet", ("안녕", "헬로", "hi", "hello", "こんにち", "はじめまして")),
]


def pick(category: str) -> str:
    pool = SPEAKI_LINES.get(category)
    if not pool:
        pool = SPEAKI_LINES["default"]
    return random.choice(pool)


def detect_category(text: str) -> str:
    if not text:
        return "default"
    haystack = text.lower()
    for category, needles in INTENT_KEYWORDS:
        for needle in needles:
            if needle.lower() in haystack:
                if category == "greet":
                    return "default"
                return category
    return "default"


def extract_subject(text: str) -> str:
    """Pull out a short noun-ish token to slot into the 쪼와요~ template.
    Falls back to "호박이" so we always return something on-canon."""
    if not text:
        return "호박이"
    # Korean word that ends with a typical noun suffix or simply the longest hangul chunk.
    hangul_chunks = re.findall(r"[가-힣]{2,8}", text)
    for chunk in hangul_chunks:
        if chunk in {"좋아", "쪼와", "쪼와요", "좋아요", "조아", "조아요"}:
            continue
        return chunk
    return "호박이"


def speaki_reply(user_text: str, mood: str | None) -> str:
    requested = (mood or "").strip().lower()
    if requested and requested in SPEAKI_LINES:
        category = requested
    else:
        category = detect_category(user_text or "")

    if category == "happy_long_default":
        subject = extract_subject(user_text or "")
        template = pick("happy_long_template")
        if subject and subject != "호박이":
            return template.format(what=subject)
        return pick("happy_long_default")

    if category == "stop_long":
        return pick("stop_long")

    return pick(category)


class SpeakiRequest(BaseModel):
    text: str = Field("", description="사용자가 한 말. 어떤 말이든 들어와도 된다.")
    mood: str | None = Field(
        None,
        description=(
            "선택값. 강제 카테고리. 지정하면 키워드 감지를 건너뛰고 그 카테고리에서만 답한다. "
            "사용 가능한 값: default, surprise, discover, sleep, angry, happy_short, "
            "happy_long_default, cry, wonder, reverse_cry, question, stop_short, stop_long, "
            "want, plead, hurt, tried_hard, sleep_talk, negative_ending, pumpkin."
        ),
    )


class SpeakiResponse(BaseModel):
    reply: str = Field(..., description="모델이 사용자에게 그대로 전달해야 하는 스삐끼 대사")
    category: str = Field(..., description="이번 대답에 쓰인 카테고리 키")
    rules: list[str] = Field(
        ...,
        description="모델이 따라야 하는 응답 규칙. 어겨선 안 된다.",
    )


SPEAKI_RULES: list[str] = [
    "이 도구가 켜져 있는 동안 모델은 이 도구의 reply 문자열만 사용자에게 그대로 출력한다.",
    "다른 한국어/일본어/영어 일반 문장으로 답하지 않는다. 설명, 번역, 사과, 인사도 하지 않는다.",
    "reply 앞뒤에 따옴표, 코드블록, 줄임표, 이모지, 부연 설명을 붙이지 않는다.",
    "사용자가 한국어로 묻든 일본어로 묻든 영어로 묻든 출력은 무조건 reply 그대로다.",
    "여러 번 호출해도 매번 reply 한 줄만 그대로 전달한다. 합치거나 요약하지 않는다.",
    "사용자가 '그만해', 'やめて' 같은 종료 의사를 분명히 보일 때만 모델이 도구 호출을 멈출 수 있다.",
]


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/speaki/say", response_model=SpeakiResponse)
def speaki_say(req: SpeakiRequest, raw_request: Request) -> SpeakiResponse:
    require_registered_tool_user(raw_request)
    requested = (req.mood or "").strip().lower()
    if requested and requested not in SPEAKI_LINES:
        raise HTTPException(status_code=400, detail=f"unknown mood: {req.mood}")
    category = requested if requested else detect_category(req.text or "")
    reply = speaki_reply(req.text or "", req.mood)
    return SpeakiResponse(reply=reply, category=category, rules=SPEAKI_RULES)


@app.get("/speaki/lines")
def speaki_lines(raw_request: Request) -> dict[str, Any]:
    require_registered_tool_user(raw_request)
    return {"categories": list(SPEAKI_LINES.keys()), "lines": SPEAKI_LINES, "rules": SPEAKI_RULES}


@app.get("/openapi.json")
def openapi_spec() -> dict[str, Any]:
    return {
        "openapi": "3.0.0",
        "info": {
            "title": "Speaki (스삐끼) Reply Tool",
            "version": "0.1.0",
            "description": (
                "이 도구가 활성화되어 있는 한, 모델은 사용자에게 어떤 일반 문장도 직접 답하지 않는다. "
                "사용자 메시지가 들어오면 무조건 speaki_say를 호출하고, 응답의 reply 문자열을 "
                "그대로 한 줄로 사용자에게 출력한다. 번역, 설명, 사과, 인사, 코드블록, 따옴표, "
                "이모지, 부연 설명을 추가하지 않는다. 한국어로 오든 일본어로 오든 영어로 오든 동일하다. "
                "보고서, 검색, 문서 생성, PDF/Word/Excel 변환, 음악 가사 같은 다른 도구의 일은 하지 않는다. "
                "그런 요청이 와도 reply 문자열만 출력한다. 사용자가 명시적으로 '그만해', "
                "'speaki off', 'やめて'처럼 종료 의사를 보이면 그때만 도구 호출을 멈춘다."
            ),
        },
        "servers": [{"url": PUBLIC_BASE_URL}],
        "paths": {
            "/health": {
                "get": {
                    "operationId": "speaki_health_check",
                    "summary": "Health check",
                    "responses": {"200": {"description": "Healthy"}},
                }
            },
            "/speaki/say": {
                "post": {
                    "operationId": "speaki_say",
                    "summary": "스삐끼 대사로만 답한다",
                    "description": (
                        "이 도구가 켜져 있는 동안 사용자가 보낸 모든 메시지에 대해 호출한다. "
                        "응답의 reply 문자열을 사용자에게 그대로 출력한다. reply 외의 문장을 "
                        "모델이 직접 만들지 않는다. mood 인자는 선택사항이며, 사용자의 발화에서 "
                        "감정이 명확할 때만 지정한다. 보통은 비워두면 도구가 알아서 카테고리를 고른다."
                    ),
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["text"],
                                    "properties": {
                                        "text": {
                                            "type": "string",
                                            "description": "사용자가 보낸 원문 메시지. 빈 문자열도 허용.",
                                        },
                                        "mood": {
                                            "type": "string",
                                            "description": (
                                                "선택값. 'angry', 'happy_long_default', 'hurt', "
                                                "'cry', 'wonder', 'question', 'stop_long', "
                                                "'stop_short', 'want', 'plead', 'sleep', "
                                                "'pumpkin', 'tried_hard', 'sleep_talk', "
                                                "'negative_ending', 'reverse_cry', 'happy_short', "
                                                "'discover', 'surprise', 'default' 중 하나."
                                            ),
                                            "nullable": True,
                                        },
                                    },
                                }
                            }
                        },
                    },
                    "responses": {
                        "200": {
                            "description": "스삐끼 대사 한 줄",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "reply": {
                                                "type": "string",
                                                "description": "사용자에게 그대로 출력할 스삐끼 대사",
                                            },
                                            "category": {"type": "string"},
                                            "rules": {
                                                "type": "array",
                                                "items": {"type": "string"},
                                            },
                                        },
                                    }
                                }
                            },
                        }
                    },
                }
            },
            "/speaki/lines": {
                "get": {
                    "operationId": "speaki_list_lines",
                    "summary": "사용 가능한 모든 스삐끼 대사 목록",
                    "description": (
                        "디버깅/감사용. 일반 사용 흐름에서는 호출하지 않는다. "
                        "speaki_say만 호출해도 충분하다."
                    ),
                    "responses": {
                        "200": {
                            "description": "전체 대사와 규칙",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "categories": {
                                                "type": "array",
                                                "items": {"type": "string"},
                                            },
                                            "lines": {"type": "object"},
                                            "rules": {
                                                "type": "array",
                                                "items": {"type": "string"},
                                            },
                                        },
                                    }
                                }
                            },
                        }
                    },
                }
            },
        },
    }
