from __future__ import annotations

import re

from models import Area, Judgement, Mode, Score


AREAS: tuple[Area, ...] = (
    "purpose",
    "scope",
    "feasibility",
    "validation",
    "deliverable",
    "logic",
)

WEAKEST_PRIORITY: tuple[Area, ...] = (
    "validation",
    "scope",
    "purpose",
    "deliverable",
    "feasibility",
    "logic",
)

JUDGEMENT_ORDER: list[Judgement] = [
    "REJECT",
    "REVISION_REQUIRED",
    "CONDITIONAL_PASS",
    "PASS",
]

KEYWORDS = {
    "purpose": ("목적", "목표", "왜", "문제", "해결", "필요", "goal", "objective", "why"),
    "scope": ("mvp", "1차", "범위", "단계", "제외", "최소 기능", "우선", "phase"),
    "feasibility": ("구현", "기술", "api", "서버", "데이터", "모델", "db", "rust", "python", "docker", "fastapi"),
    "validation": ("검증", "기준", "지표", "metric", "정확도", "성공 기준", "실패 기준", "latency", "성능", "테스트"),
    "deliverable": ("결과물", "코드", "api", "문서", "모델", "보고서", "ui", "cli", "서버", "테스트 코드"),
    "logic": ("1.", "2.", "3.", "-", "##", "###", "단계", "구성", "흐름"),
}

SCOPE_PENALTIES = ("전체", "모든", "완전 자동화", "전부", "다")
VALIDATION_NUMERIC_RE = re.compile(r"(\d+|\d+\.\d+|%|퍼센트|ms|sec|초|개|건|threshold|p95)", re.IGNORECASE)


def score_text(text: str) -> Score:
    normalized = text.strip()
    lower = normalized.lower()

    purpose = _keyword_score(lower, KEYWORDS["purpose"])
    if _has_clear_purpose_sentence(normalized):
        purpose = 2

    scope = _keyword_score(lower, KEYWORDS["scope"])
    if any(word in lower for word in (item.lower() for item in SCOPE_PENALTIES)):
        scope = max(0, scope - 1)
    if "mvp" in lower or "1차" in lower or "최소 기능" in lower:
        scope = max(scope, 2)

    feasibility = _keyword_score(lower, KEYWORDS["feasibility"])
    validation = _keyword_score(lower, KEYWORDS["validation"])
    if VALIDATION_NUMERIC_RE.search(lower):
        validation = min(2, validation + 1)

    deliverable = _keyword_score(lower, KEYWORDS["deliverable"])
    logic = _score_logic(normalized)

    total = purpose + scope + feasibility + validation + deliverable + logic
    return Score(
        purpose=purpose,
        scope=scope,
        feasibility=feasibility,
        validation=validation,
        deliverable=deliverable,
        logic=logic,
        total=total,
    )


def decide_judgement(total: int, mode: Mode) -> Judgement:
    if total <= 3:
        judgement: Judgement = "REJECT"
    elif total <= 6:
        judgement = "REVISION_REQUIRED"
    elif total <= 9:
        judgement = "CONDITIONAL_PASS"
    else:
        judgement = "PASS"

    if mode == "strict" and total != 12:
        judgement = _downgrade(judgement)

    return judgement


def get_weakest_area(score: Score) -> Area:
    values = score.model_dump()
    min_score = min(int(values[area]) for area in AREAS)
    for area in WEAKEST_PRIORITY:
        if int(values[area]) == min_score:
            return area
    return "validation"


def fixed_assessment(text: str, mode: Mode) -> dict:
    score = score_text(text)
    return {
        "judgement": decide_judgement(score.total, mode),
        "score": score,
        "weakest_area": get_weakest_area(score),
    }


def _keyword_score(text: str, keywords: tuple[str, ...]) -> int:
    hits = sum(1 for keyword in keywords if keyword.lower() in text)
    if hits == 0:
        return 0
    if hits >= 2:
        return 2
    return 1


def _has_clear_purpose_sentence(text: str) -> bool:
    sentences = re.split(r"[.!?\n]+", text)
    for sentence in sentences:
        compact = sentence.strip()
        if not compact:
            continue
        has_purpose = any(keyword in compact.lower() for keyword in KEYWORDS["purpose"])
        clear_length = 12 <= len(compact) <= 120
        has_copula = any(token in compact for token in ("이다", "것이다", "한다", "위해", "만든다"))
        if has_purpose and clear_length and has_copula:
            return True
    return False


def _score_logic(text: str) -> int:
    compact = text.strip()
    if not compact:
        return 0
    markers = sum(1 for marker in KEYWORDS["logic"] if marker in compact)
    non_empty_lines = [line for line in compact.splitlines() if line.strip()]
    has_paragraphs = "\n\n" in compact or len(non_empty_lines) >= 3
    has_list = bool(re.search(r"(^|\n)\s*(-|\d+[.)])\s+", compact))

    if len(compact) < 40:
        return min(1, 1 if markers else 0)
    if markers >= 2 or has_paragraphs or has_list:
        return 2
    return 1


def _downgrade(judgement: Judgement) -> Judgement:
    index = JUDGEMENT_ORDER.index(judgement)
    return JUDGEMENT_ORDER[max(0, index - 1)]
