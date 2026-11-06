"""설정 파일(.json) — 사업개요·연체료 규정·기본 정보·비고 규칙을 한 파일로 저장·불러오기.

웹 첫 화면에서 불러오면 입력칸이 채워지고, 결과 zip에는 이번에 쓴 설정이 `설정.json`으로 들어간다.
성명·연락처는 개인정보라 설정 파일에 넣지 않는다(학기도 직접 고른다).
"""
from __future__ import annotations

import json
from typing import Any

from src.rules_config import DEFAULT_RULES, RuleSet, RulesError, build_rules

SETTINGS_VERSION = 1
SETTINGS_NOTE = (
    "수입지출내역서 자동 작성 설정 파일입니다. 웹 첫 화면의 '설정 파일 불러오기'로 올리면 "
    "입력칸이 채워집니다. 비고규칙의 문장 틀에서 {중괄호} 칸은 자동으로 채워지는 자리입니다."
)


def parse_rules_text(text: str) -> RuleSet:
    """웹 '비고 규칙' 칸(JSON). 비어 있으면 기본 규칙. 설정 파일 전체를 붙여 넣어도 된다."""
    stripped = (text or "").strip()
    if not stripped:
        return build_rules()
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise RulesError(
            f"비고 규칙을 읽지 못했습니다: {exc.lineno}번째 줄 {exc.colno}번째 글자 근처 "
            f"({_json_hint(exc)}). 쉼표·따옴표·중괄호 짝을 확인해 주세요."
        ) from exc
    if isinstance(data, dict) and isinstance(data.get("비고규칙"), dict):
        data = data["비고규칙"]
    return build_rules(data)


def _json_hint(exc: json.JSONDecodeError) -> str:
    msg = exc.msg
    hints = {
        "Expecting ',' delimiter": "쉼표가 빠졌습니다",
        "Expecting property name enclosed in double quotes": "항목 이름은 큰따옴표로 감싸야 하고, 마지막 항목 뒤에는 쉼표가 없어야 합니다",
        "Expecting value": "값이 비어 있거나 따옴표가 빠졌습니다",
        "Unterminated string starting at": "따옴표가 닫히지 않았습니다",
        "Expecting ':' delimiter": "항목 이름 뒤에 : 이 빠졌습니다",
        "Extra data": "끝에 불필요한 글자가 있습니다",
    }
    for key, hint in hints.items():
        if msg.startswith(key):
            return hint
    return msg


def default_rules_text() -> str:
    return json.dumps(DEFAULT_RULES, ensure_ascii=False, indent=2)


def build_settings(
    *,
    semester_label: str,
    author: dict[str, str] | None,
    locker_deposit_remark: str,
    film_fee_per_sheet: int | str,
    business_overview: str,
    overdue_rules_text: str,
    rules: RuleSet,
) -> dict[str, Any]:
    # 성명·연락처는 개인정보라 설정 파일에 넣지 않는다(학기마다 직접 적음)
    safe_author = {k: v for k, v in (author or {}).items() if k not in ("연락처", "성명") and v}
    return {
        "_설명": SETTINGS_NOTE,
        "설정버전": SETTINGS_VERSION,
        "기본정보": {
            "학기명": semester_label or "",
            "작성자": {
                "직위": safe_author.get("직위", ""),
                "소속": safe_author.get("소속", ""),
            },
            "사물함비고문구": locker_deposit_remark,
            "필름1장가격": str(film_fee_per_sheet),
        },
        "사업개요": business_overview.strip(),
        "연체료규정": overdue_rules_text.strip(),
        "비고규칙": rules.source,
    }


def settings_to_json(settings: dict[str, Any]) -> str:
    return json.dumps(settings, ensure_ascii=False, indent=2) + "\n"


def example_settings() -> dict[str, Any]:
    """기본값 설정 (개인정보 없음, 학기는 비움)."""
    from src.semester_config import DEFAULT_FILM_FEE_PER_SHEET, DEFAULT_LOCKER_DEPOSIT_REMARK
    from web.semester_inputs import EXAMPLE_AUTHOR, EXAMPLE_OVERDUE_RULES, EXAMPLE_OVERVIEW, EXAMPLE_SEMESTER

    return build_settings(
        semester_label=EXAMPLE_SEMESTER,
        author=dict(EXAMPLE_AUTHOR),
        locker_deposit_remark=DEFAULT_LOCKER_DEPOSIT_REMARK,
        film_fee_per_sheet=DEFAULT_FILM_FEE_PER_SHEET,
        business_overview=EXAMPLE_OVERVIEW,
        overdue_rules_text=EXAMPLE_OVERDUE_RULES,
        rules=build_rules(),
    )
