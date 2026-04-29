from __future__ import annotations

import os
import json
from typing import Any

import httpx

from models import WonConfirmRequest


DEFAULT_GEMMA_API_URL = "http://192.168.100.13:8000/v1/chat/completions"
DEFAULT_GEMMA_MODEL = "gemma-4-31b-it"

SYSTEM_PROMPT = """너는 교수 스타일의 검토 코멘트를 생성하는 모델이다.

아래 규칙을 반드시 지켜라.

[고정 규칙]
- judgement, score, weakest_area는 절대 수정하지 마라.
- JSON만 출력한다. 설명 금지.
- 한국어로만 작성한다.
- 문장은 짧고 직설적으로 쓴다.
- 감정 표현 금지.
- 욕설 금지.
- 반드시 한 줄은 강하게 찌른다. 이 문장은 sarcastic_slot이다.
- 반드시 마지막 줄은 조건부 가능성으로 끝낸다.

[톤]
- 차갑다.
- 간결하다.
- 단정적으로 말한다.
- 칭찬은 최소화한다.
- 부정 -> 이유 -> 수정 -> 조건부 가능성 순서로 쓴다.

[sarcastic_slot 규칙]
- 한 문장
- 50자 이하
- 반박하기 어렵게 작성
- weakest_area를 가장 강하게 지적

[필수 포함 조건]
- validation 점수 0이면 반드시 "검증 기준이 없음." 포함
- scope 점수 <= 1이면 반드시 "범위가 큼. 줄여야 함." 포함
- purpose 점수 = 0이면 반드시 "목적이 흐림." 포함
- deliverable 점수 = 0이면 반드시 "결과물이 불명확함." 포함

[출력 형식]
{
  "sarcastic_slot": "...",
  "professor_comment": "...",
  "problems": ["...", "..."],
  "revision_orders": ["...", "..."],
  "rewritten_version": "..."
}

[professor_comment 구조]
판정: {judgement_한글}

방향은 맞음.

근데 지금 문서는 계획이라고 보긴 어려움. {sarcastic_slot}

{문제 요약}

이 상태로 시작하면 중간에 멈춤.

수정:
1. ...
2. ...
3. ...

{마지막 한 줄: 조건부 가능성}

[마지막 줄 규칙]
- 위로 금지
- "~하면 가능함" 형태
"""


async def call_gemma(request: WonConfirmRequest, fixed: dict) -> dict[str, Any]:
    api_url = os.getenv("GEMMA_API_URL") or os.getenv("DOCUMENT_FILLER_API_URL") or DEFAULT_GEMMA_API_URL
    model = os.getenv("GEMMA_MODEL") or os.getenv("DOCUMENT_FILLER_MODEL_ID") or DEFAULT_GEMMA_MODEL
    api_key = os.getenv("GEMMA_API_KEY", "").strip()

    user_prompt = build_user_prompt(request, fixed)
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.25,
        "top_p": 0.8,
        "max_tokens": 700,
    }
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    timeout = httpx.Timeout(float(os.getenv("GEMMA_TIMEOUT_SECONDS", "30")))
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(api_url, headers=headers, json=payload)
        response.raise_for_status()
        body = response.json()

    choices = body.get("choices") or []
    if not choices:
        raise ValueError("Gemma response has no choices")
    message = choices[0].get("message") or {}
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise ValueError("Gemma response has no content")
    from utils import extract_json_object

    return extract_json_object(content)


def build_user_prompt(request: WonConfirmRequest, fixed: dict) -> str:
    score_json = json.dumps(fixed["score"].model_dump(), ensure_ascii=False)
    return "\n".join(
        [
            "아래 입력을 바탕으로 JSON만 생성해라.",
            "",
            "text:",
            request.text,
            "",
            "judgement:",
            fixed["judgement"],
            "",
            "score:",
            score_json,
            "",
            "weakest_area:",
            fixed["weakest_area"],
            "",
            "mode:",
            request.mode,
            "",
            "rewrite:",
            json.dumps(request.rewrite),
        ]
    )
