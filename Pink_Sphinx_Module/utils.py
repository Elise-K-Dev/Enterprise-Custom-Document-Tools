from __future__ import annotations

import json
from typing import Any

from fallback import build_fallback_response
from models import Score, WonConfirmRequest, WonConfirmResponse


REQUIRED_LLM_FIELDS = (
    "sarcastic_slot",
    "professor_comment",
    "problems",
    "revision_orders",
    "rewritten_version",
)


def extract_json_object(raw: str) -> dict[str, Any]:
    if not raw:
        raise ValueError("empty LLM response")

    fenced = raw.strip()
    if fenced.startswith("```"):
        lines = fenced.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        fenced = "\n".join(lines).strip()

    start = fenced.find("{")
    end = fenced.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("JSON object not found")
    return json.loads(fenced[start : end + 1])


def validate_and_merge_llm_output(
    llm_output: dict[str, Any],
    fixed: dict,
    request: WonConfirmRequest,
) -> WonConfirmResponse:
    fallback = build_fallback_response(request, fixed)
    data: dict[str, Any] = {}

    for field in REQUIRED_LLM_FIELDS:
        value = llm_output.get(field)
        if field in {"problems", "revision_orders"}:
            data[field] = _string_list(value) or getattr(fallback, field)
        elif isinstance(value, str) and value.strip():
            data[field] = value.strip()
        else:
            data[field] = getattr(fallback, field)

    data["sarcastic_slot"] = _limit_chars(data["sarcastic_slot"], 50)
    data["problems"] = _merge_required_sentences(data["problems"], fallback.problems)
    data["revision_orders"] = data["revision_orders"] or fallback.revision_orders
    data["professor_comment"] = _ensure_comment_contains_required_problems(
        data["professor_comment"],
        data["problems"],
    )
    data["professor_comment"] = _limit_lines(data["professor_comment"], 12)
    if not request.rewrite:
        data["rewritten_version"] = ""

    return WonConfirmResponse(
        judgement=fixed["judgement"],
        score=_score_from_fixed(fixed["score"]),
        weakest_area=fixed["weakest_area"],
        sarcastic_slot=data["sarcastic_slot"],
        professor_comment=data["professor_comment"],
        problems=data["problems"],
        revision_orders=data["revision_orders"],
        rewritten_version=data["rewritten_version"],
    )


def _score_from_fixed(value: Score | dict[str, Any]) -> Score:
    if isinstance(value, Score):
        return value
    return Score(**value)


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    out = [str(item).strip() for item in value if str(item).strip()]
    return out


def _limit_chars(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[:limit].rstrip()


def _limit_lines(value: str, limit: int) -> str:
    lines = value.splitlines()
    if len(lines) <= limit:
        return value
    last = lines[-1]
    if "가능함" in last and limit >= 2:
        return "\n".join(lines[: limit - 1] + [last]).rstrip()
    return "\n".join(lines[:limit]).rstrip()


def _merge_required_sentences(primary: list[str], required: list[str]) -> list[str]:
    out = list(primary)
    for sentence in required:
        if sentence not in out:
            out.append(sentence)
    return out


def _ensure_comment_contains_required_problems(comment: str, problems: list[str]) -> str:
    missing = [problem for problem in problems if problem not in comment]
    if not missing:
        return comment
    lines = comment.rstrip().splitlines()
    insert_at = min(6, len(lines))
    lines[insert_at:insert_at] = [" ".join(missing)]
    return "\n".join(lines)
