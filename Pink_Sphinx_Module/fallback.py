from __future__ import annotations

import re

from models import Area, Score, WonConfirmRequest, WonConfirmResponse


JUDGEMENT_KO = {
    "PASS": "통과",
    "CONDITIONAL_PASS": "조건부 통과",
    "REVISION_REQUIRED": "수정 필요",
    "REJECT": "반려",
}

SARCASM_BY_AREA: dict[Area, str] = {
    "purpose": "목적이 흐리면 나머지는 다 장식임.",
    "scope": "지금 범위로는 완주 못 함.",
    "feasibility": "구현이 아니라 기대에 가까움.",
    "validation": "성공 기준이 없으면 실패도 정의 안 됨.",
    "deliverable": "결과물이 안 보이면 진행도 안 보임.",
    "logic": "흐름이 아니라 생각 조각 모음임.",
}

AREA_LABEL = {
    "purpose": "목적",
    "scope": "범위",
    "feasibility": "구현 가능성",
    "validation": "검증 기준",
    "deliverable": "결과물",
    "logic": "논리 흐름",
}


def build_fallback_response(request: WonConfirmRequest, fixed: dict) -> WonConfirmResponse:
    score: Score = fixed["score"]
    judgement = fixed["judgement"]
    weakest_area: Area = fixed["weakest_area"]
    sarcastic_slot = SARCASM_BY_AREA[weakest_area]
    problems = build_problem_sentences(score)
    revision_orders = build_revision_orders(score)
    professor_comment = build_professor_comment(judgement, sarcastic_slot, problems, revision_orders)
    rewritten_version = build_rewritten_version(request.text, request.rewrite)
    return WonConfirmResponse(
        judgement=judgement,
        score=score,
        weakest_area=weakest_area,
        sarcastic_slot=sarcastic_slot,
        professor_comment=professor_comment,
        problems=problems,
        revision_orders=revision_orders,
        rewritten_version=rewritten_version,
    )


def build_problem_sentences(score: Score) -> list[str]:
    problems: list[str] = []
    if score.validation == 0:
        problems.append("검증 기준이 없음.")
    if score.scope <= 1:
        problems.append("범위가 큼. 줄여야 함.")
    if score.purpose == 0:
        problems.append("목적이 흐림.")
    if score.deliverable == 0:
        problems.append("결과물이 불명확함.")
    if score.feasibility == 0:
        problems.append("구현 근거가 없음.")
    if score.logic == 0:
        problems.append("문서 흐름이 없음.")
    if not problems:
        problems.append("핵심 요소는 있으나 실행 기준이 더 선명해야 함.")
    return problems


def build_revision_orders(score: Score) -> list[str]:
    orders: list[str] = []
    if score.purpose < 2:
        orders.append("목적을 한 문장으로 정리")
    if score.scope < 2:
        orders.append("MVP 범위를 명시")
    if score.validation < 2:
        orders.append("성공 기준을 숫자로 정의")
    if score.deliverable < 2:
        orders.append("최종 결과물을 파일, API, 테스트 단위로 명시")
    if score.feasibility < 2:
        orders.append("사용 기술과 구현 방식을 구체화")
    if score.logic < 2:
        orders.append("단계별 흐름으로 재구성")
    return orders[:6] or ["현재 구조를 유지하되 검증 기준을 보강"]


def build_professor_comment(
    judgement: str,
    sarcastic_slot: str,
    problems: list[str],
    revision_orders: list[str],
) -> str:
    selected_orders = revision_orders[:3]
    while len(selected_orders) < 3:
        selected_orders.append("성공 기준을 숫자로 정의")
    return "\n".join(
        [
            f"판정: {JUDGEMENT_KO.get(judgement, judgement)}",
            "",
            "방향은 맞음.",
            "",
            f"근데 지금 문서는 계획이라고 보긴 어려움. {sarcastic_slot}",
            "",
            " ".join(problems),
            "",
            "이 상태로 시작하면 중간에 멈춤.",
            "",
            "수정:",
            f"1. {selected_orders[0]}",
            f"2. {selected_orders[1]}",
            f"3. {selected_orders[2]}",
            "",
            "이 정도 정리되면 진행 가능함.",
        ]
    )


def build_rewritten_version(text: str, rewrite: bool) -> str:
    if not rewrite:
        return ""

    purpose = _find_sentence(text, ("목적", "목표", "문제", "해결", "필요"))
    scope = _find_sentence(text, ("범위", "1차", "MVP", "최소", "제외", "단계"))
    implementation = _find_sentence(text, ("구현", "FastAPI", "API", "서버", "Python", "Rust", "Docker", "DB"))
    validation = _find_sentence(text, ("검증", "기준", "테스트", "정확도", "성공", "실패", "지표"))
    deliverable = _find_sentence(text, ("결과물", "코드", "문서", "보고서", "UI", "CLI", "테스트 코드"))

    return "\n\n".join(
        [
            "목적:\n- " + purpose,
            "범위:\n- " + scope,
            "구현:\n- " + implementation,
            "검증:\n- " + validation,
            "결과물:\n- " + deliverable,
        ]
    )


def _find_sentence(text: str, keywords: tuple[str, ...]) -> str:
    sentences = [part.strip() for part in re.split(r"[.!?\n。]+", text) if part.strip()]
    for sentence in sentences:
        lower = sentence.lower()
        if any(keyword.lower() in lower for keyword in keywords):
            return sentence
    return "추가 정의 필요"

