"""비고 규칙 설정 — 코드에 박혀 있던 업무 규칙을 한곳에 모은다.

기본값(DEFAULT_RULES)은 이전 버전의 출력과 글자 하나까지 같게 맞춰 두었다.
사용자는 바꾸고 싶은 항목만 적으면 되고, 적지 않은 항목은 기본값을 쓴다.
"""
from __future__ import annotations

import copy
import string
from dataclasses import dataclass
from typing import Any

from .utils import josa_ro_euro

# 사업 종류: 화면·설정 파일에서 쓰는 한글 이름 ↔ 내부 역할
ROLE_BY_LABEL = {"사물함": "locker", "대여": "rental", "학생회비": "student_fee", "일반": "general"}
LABEL_BY_ROLE = {v: k for k, v in ROLE_BY_LABEL.items()}

# 비고 문장 틀마다 쓸 수 있는 자리표시자
TEMPLATE_FIELDS: dict[str, tuple[str, ...]] = {
    "대상": ("대여자",),
    "대상_대납": ("대여자", "입금자", "조사"),
    "연체": ("대상", "물품", "일수", "금액"),
    "연체_검수": ("대상", "물품", "일수", "금액"),
    "연체_일수불명": ("대상", "물품", "금액"),
    "필름값": ("대상", "물품", "장수", "금액"),
    "손망실비": ("대상", "물품", "금액"),
    "감면": ("대상", "물품", "일수", "금액"),
    "감면_일수불명": ("대상", "물품", "금액"),
    "환불": ("이름", "사유", "조사"),
}

DEFAULT_RULES: dict[str, Any] = {
    # 비고를 미리 채우는 주요 사업만 알아본다(나머지는 일반 = 비고 비움). 띄어쓰기 무시.
    "사업종류_키워드": {
        "학생회비": ["총학", "학생회비"],
        "사물함": ["사물함배부"],
        "대여": ["물품대여"],
    },
    "환불페이지_키워드": ["환불"],
    # 규정상 단가(3,000원, 피크닉용품)와 실제로 받는 금액(그 외 2,000원)이 자주 달라 항상 검수필요로 올리는 물품
    "연체료_항상검수_물품": ["삼각대", "돗자리", "보드게임"],
    "환불사유_표현": {
        "당첨등록포기": "당첨 등록 포기",
        "두번송금": "두 번 송금",
        "중복입금": "두 번 송금",
        "중복등록포기": "중복 등록 포기",
        "규칙미준수": "규칙 미준수",
    },
    "환불사유_묶음": {
        "당첨 등록 포기": ["당첨", "등록 포기로 인하여 환불 진행"],
        "중복 등록 포기": ["중복", "등록 포기로 인하여 환불 진행"],
    },
    "연체료비고_분류": {
        "필름값": ["필름값"],
        "손망실비": ["손망실비"],
        "감면": ["감면"],
    },
    "환불수수료_안내": "환불 단가는 수수료 포함 합본금액. 양식에 맞게 원금/수수료 분리 검토.",
    "비고문장": {
        "대상": "{대여자}님",
        "대상_대납": "{대여자}(‘{입금자}’{조사} 입금)님",
        "연체": "{대상} {물품} 대여 후 {일수}일 연체되어 연체료 {금액}원 부과",
        # 일수는 계산됐지만 확인할 점이 있는 경우 — 문서에는 표시하지 않고 검수목록에만 남긴다.
        "연체_검수": "{대상} {물품} 대여 후 {일수}일 연체되어 연체료 {금액}원 부과",
        "연체_일수불명": "{대상} {물품} 대여 후 연체되어 연체료 {금액}원 부과 (연체일수 검수필요)",
        "필름값": "{대상} 폴라로이드 필름 {장수}장 추가로 소모하여 {금액}원 부과",
        "손망실비": "{대상} {물품} 손망실비 {금액}원 부과",
        "감면": "{대상} {물품} 대여 후 {일수}일 연체되어 연체료 {금액}원 감면",
        "감면_일수불명": "{대상} {물품} 대여 후 연체되어 연체료 {금액}원 감면 (검수필요)",
        "환불": "{이름}님은 {사유}{조사} 인하여 환불 진행",
    },
}

RULE_KEYS = tuple(DEFAULT_RULES)
# 예전 설정 파일에만 있을 수 있는, 지금은 쓰지 않는 항목 — 오류 없이 무시한다.
RETIRED_KEYS = {"구분_표기"}


class RulesError(ValueError):
    """비고 규칙 설정이 잘못됨 — 사용자가 고칠 수 있는 안내 문구를 담는다."""


def _compact(text: Any) -> str:
    return "".join(str(text).split())


@dataclass(frozen=True)
class RuleSet:
    role_keywords: dict[str, tuple[str, ...]]
    refund_page_keywords: tuple[str, ...]
    refund_reason_phrases: dict[str, str]  # 공백 뺀 원문 → 문장 표현
    refund_groups: dict[str, tuple[str, str]]
    rental_note_kinds: dict[str, tuple[str, ...]]  # 분류 → 공백 뺀 비고 문구들
    refund_fee_advisory: str
    overdue_watch: tuple[str, ...]  # 연체료를 항상 확인할 물품(공백 뺀 낱말)
    templates: dict[str, str]
    source: dict[str, Any]  # 설정 파일로 다시 저장할 때 쓰는 원본(기본값과 합친 것)

    # ── 판단 ──
    def role_for_name(self, name: str, business_no: int) -> str:
        if business_no == 0:
            return "student_fee"
        compact = _compact(name)
        for role, keywords in self.role_keywords.items():
            if any(_compact(k) in compact for k in keywords):
                return role
        return "general"

    def is_refund_page(self, overview: str | None) -> bool:
        text = overview or ""
        return any(k in text for k in self.refund_page_keywords)

    def refund_phrase(self, reason: str | None) -> str:
        if not reason:
            return ""
        return self.refund_reason_phrases.get(_compact(reason), reason.strip())

    def rental_note_kind(self, note: str | None) -> str:
        compact = _compact(note or "")
        for kind, words in self.rental_note_kinds.items():
            if compact and compact in words:
                return kind
        return "연체"

    # ── 문장 ──
    def render(self, name: str, **values: Any) -> str:
        return self.templates[name].format(**{k: "" if v is None else v for k, v in values.items()})

    def subject(self, renter: str, depositor: str | None = None, josa: str | None = None) -> str:
        if depositor is None:
            return self.render("대상", 대여자=renter)
        return self.render(
            "대상_대납", 대여자=renter, 입금자=depositor, 조사=josa or josa_ro_euro(depositor)
        )


def _as_str_list(value: Any, where: str) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list) or not all(isinstance(v, (str, int, float)) for v in value):
        raise RulesError(f"'{where}'는 글자 목록이어야 합니다. 예: [\"환불\", \"반환\"]")
    items = [str(v).strip() for v in value if str(v).strip()]
    return items


def _as_dict(value: Any, where: str) -> dict:
    if not isinstance(value, dict):
        raise RulesError(f"'{where}'는 {{\"이름\": 값}} 형태여야 합니다.")
    return value


def _check_template(name: str, text: Any) -> str:
    if not isinstance(text, str) or not text.strip():
        raise RulesError(f"비고문장 '{name}'이 비어 있습니다.")
    allowed = TEMPLATE_FIELDS[name]
    try:
        used = [field for _, field, _, _ in string.Formatter().parse(text) if field is not None]
    except ValueError as exc:
        raise RulesError(
            f"비고문장 '{name}'의 중괄호가 짝이 맞지 않습니다: {text!r}"
        ) from exc
    unknown = [f for f in used if f not in allowed]
    if unknown:
        raise RulesError(
            f"비고문장 '{name}'에 쓸 수 없는 칸 {', '.join('{' + u + '}' for u in unknown)}이 있습니다. "
            f"쓸 수 있는 칸: {', '.join('{' + a + '}' for a in allowed)}"
        )
    return text


# 예전 기본값 그대로 저장된 설정 파일은 지금 기본값을 쓰게 한다(직접 고친 값은 그대로 둔다).
_OLD_DEFAULTS = {
    "사업종류_키워드": {"학생회비": ["총학", "학생회비"], "사물함": ["사물함"], "대여": ["대여", "물품대여"]},
}


def merge_with_defaults(user: dict[str, Any] | None) -> dict[str, Any]:
    """사용자가 적은 항목만 기본값 위에 덮는다. 사전형 항목은 키 단위로 합친다."""
    merged = copy.deepcopy(DEFAULT_RULES)
    if not user:
        return merged
    user = {k: v for k, v in user.items() if _OLD_DEFAULTS.get(k) != v}
    _as_dict(user, "비고규칙")
    user = {k: v for k, v in user.items() if k not in RETIRED_KEYS}
    unknown = [k for k in user if k not in DEFAULT_RULES and not str(k).startswith("_")]
    if unknown:
        raise RulesError(
            f"비고규칙에 모르는 항목이 있습니다: {', '.join(unknown)}. "
            f"쓸 수 있는 항목: {', '.join(RULE_KEYS)}"
        )
    for key, value in user.items():
        if str(key).startswith("_"):
            continue
        if key in ("비고문장", "환불사유_표현", "환불사유_묶음", "연체료비고_분류", "사업종류_키워드"):
            merged[key] = {**merged[key], **_as_dict(value, key)}
        else:
            merged[key] = value
    return merged


def build_rules(user: dict[str, Any] | None = None) -> RuleSet:
    data = merge_with_defaults(user)

    role_keywords: dict[str, tuple[str, ...]] = {}
    for label, words in _as_dict(data["사업종류_키워드"], "사업종류_키워드").items():
        if label not in ROLE_BY_LABEL:
            raise RulesError(
                f"사업종류_키워드의 '{label}'은 모르는 사업 종류입니다. "
                f"쓸 수 있는 종류: {', '.join(ROLE_BY_LABEL)}"
            )
        role_keywords[ROLE_BY_LABEL[label]] = tuple(_as_str_list(words, f"사업종류_키워드.{label}"))

    phrases = {
        _compact(k): str(v).strip()
        for k, v in _as_dict(data["환불사유_표현"], "환불사유_표현").items()
        if str(v).strip()
    }

    groups: dict[str, tuple[str, str]] = {}
    for reason, pair in _as_dict(data["환불사유_묶음"], "환불사유_묶음").items():
        if pair in (None, "", []):
            continue  # 기본 묶음을 끄고 싶을 때
        items = _as_str_list(pair, f"환불사유_묶음.{reason}")
        if len(items) != 2:
            raise RulesError(
                f"환불사유_묶음 '{reason}'은 [이름 뒤에 붙일 말, 다음 줄 문장] 두 개여야 합니다."
            )
        groups[str(reason).strip()] = (items[0], items[1])

    note_kinds: dict[str, tuple[str, ...]] = {}
    for kind, words in _as_dict(data["연체료비고_분류"], "연체료비고_분류").items():
        if kind not in ("필름값", "손망실비", "감면"):
            raise RulesError(
                f"연체료비고_분류의 '{kind}'은 모르는 분류입니다. 쓸 수 있는 분류: 필름값, 손망실비, 감면"
            )
        note_kinds[kind] = tuple(_compact(w) for w in _as_str_list(words, f"연체료비고_분류.{kind}"))

    templates_raw = _as_dict(data["비고문장"], "비고문장")
    unknown_t = [k for k in templates_raw if k not in TEMPLATE_FIELDS]
    if unknown_t:
        raise RulesError(
            f"비고문장에 모르는 이름이 있습니다: {', '.join(unknown_t)}. "
            f"쓸 수 있는 이름: {', '.join(TEMPLATE_FIELDS)}"
        )
    templates = {name: _check_template(name, templates_raw[name]) for name in TEMPLATE_FIELDS}

    overdue_watch = tuple(_compact(k) for k in _as_str_list(data["연체료_항상검수_물품"], "연체료_항상검수_물품") if _compact(k))
    refund_keywords = tuple(_as_str_list(data["환불페이지_키워드"], "환불페이지_키워드"))
    if not refund_keywords:
        raise RulesError("환불페이지_키워드는 하나 이상 있어야 합니다.")

    return RuleSet(
        role_keywords=role_keywords,
        refund_page_keywords=refund_keywords,
        refund_reason_phrases=phrases,
        refund_groups=groups,
        rental_note_kinds=note_kinds,
        refund_fee_advisory=str(data["환불수수료_안내"]).strip(),
        overdue_watch=overdue_watch,
        templates=templates,
        source=data,
    )


DEFAULT_RULESET = build_rules()
