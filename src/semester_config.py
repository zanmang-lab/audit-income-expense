"""학기별 설정 — 사업 역할·규정·출력 접두어를 한곳에서 관리."""
from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from .config_gen import OverdueRulesConfig, parse_overdue_rules_data
from .rules_config import DEFAULT_RULESET, ROLE_BY_LABEL, RuleSet, build_rules

VALID_ROLES = frozenset({"student_fee", "locker", "rental", "general"})

DEFAULT_LOCKER_DEPOSIT_REMARK = (
    "전자 사물함은 대여비 4,000원, 일반 사물함은 대여비 2,000원"
)
DEFAULT_LOCKER_REFUND_ADVISORY = (
    "환불 단가는 수수료 포함 합본금액. 양식에 맞게 원금/수수료 분리 검토."
)
DEFAULT_FILM_FEE_PER_SHEET = 1000
DEFAULT_LEDGER_SHEET = "DATA (2)"
DEFAULT_LABEL = "수입지출내역서"
AUTHOR_FIELDS = ("성명", "직위", "소속", "연락처")

# 하위 호환: 기존 사업번호 체계
LEGACY_ROLE_BY_NUMBER: dict[str, str] = {
    "0": "student_fee",
    "1": "locker",
    "2": "locker",
    "3": "rental",
    "9": "locker",
}


@dataclass
class BusinessEntry:
    name: str
    role: str = "general"


@dataclass
class SemesterConfig:
    label: str = DEFAULT_LABEL
    ledger_sheet: str = DEFAULT_LEDGER_SHEET
    businesses: dict[int, BusinessEntry] = field(default_factory=dict)
    locker_deposit_remark: str = DEFAULT_LOCKER_DEPOSIT_REMARK
    locker_refund_advisory: str = DEFAULT_LOCKER_REFUND_ADVISORY
    film_fee_per_sheet: int = DEFAULT_FILM_FEE_PER_SHEET
    overdue_rules: OverdueRulesConfig = field(
        default_factory=lambda: parse_overdue_rules_data(
            {"default_per_day": 2000, "categories": []}
        )
    )
    # 작성자 정보 — 양식의 '성명/직위/소속/연락처' 라벨 오른쪽 칸에 채운다.
    author: dict[str, str] = field(default_factory=dict)
    # 비고 규칙(문장 틀·키워드 등) — src/rules_config.py
    rules: RuleSet = field(default_factory=lambda: DEFAULT_RULESET)

    def role_for(self, business_no: int) -> str:
        entry = self.businesses.get(business_no)
        return entry.role if entry else "general"

    def name_for(self, business_no: int) -> str:
        entry = self.businesses.get(business_no)
        return entry.name if entry else ""

    def name_map(self) -> dict[str, str]:
        return {str(no): entry.name for no, entry in sorted(self.businesses.items())}

    def business_numbers_with_role(self, role: str) -> set[int]:
        return {no for no, entry in self.businesses.items() if entry.role == role}


def infer_role_from_name(name: str, business_no: int, rules: RuleSet | None = None) -> str:
    """사업명 키워드로 역할 추론 (사물함·대여·총학 등). 키워드는 비고규칙 '사업종류_키워드'."""
    return (rules or DEFAULT_RULESET).role_for_name(name, business_no)


def normalize_role(value: Any) -> str | None:
    """'사물함' / 'locker' 둘 다 받는다. 모르는 값이면 None."""
    if value is None:
        return None
    text = str(value).strip()
    if text in ROLE_BY_LABEL:
        return ROLE_BY_LABEL[text]
    if text in ROLE_BY_LABEL.values():
        return text
    return None


def load_server_defaults() -> SemesterConfig:
    """서버·CLI용 내부 기본값 (연체료 계산 규칙·사물함 비고 문구 등)."""
    return default_semester_config()


def semester_config_from_business_names(
    name_map: dict[str, str],
    roles: dict[str, str] | None = None,
    rules: RuleSet | None = None,
) -> SemesterConfig:
    """
    사업개요(번호. 사업명)만으로 엔진 설정을 만든다.
    roles에 사업 종류가 직접 지정돼 있으면 그대로 쓰고, 없으면 사업명 키워드로 추론한다.
    """
    rules = rules or DEFAULT_RULESET
    roles = roles or {}
    if not name_map:
        raise ValueError(
            "사업개요에 '번호. 사업명' 형식으로 한 줄 이상 입력해 주세요."
        )

    businesses: dict[int, BusinessEntry] = {}
    for key, name in name_map.items():
        try:
            business_no = int(key)
        except ValueError as exc:
            raise ValueError(f"사업번호 '{key}'는 정수여야 합니다.") from exc
        businesses[business_no] = BusinessEntry(
            name=name,
            role=roles.get(key) or infer_role_from_name(name, business_no, rules),
        )

    return SemesterConfig(
        label=DEFAULT_LABEL,
        ledger_sheet=DEFAULT_LEDGER_SHEET,
        rules=rules,
        businesses=businesses,
        locker_deposit_remark=DEFAULT_LOCKER_DEPOSIT_REMARK,
        locker_refund_advisory=DEFAULT_LOCKER_REFUND_ADVISORY,
        film_fee_per_sheet=DEFAULT_FILM_FEE_PER_SHEET,
    )


def _parse_businesses(raw: Any, rules: RuleSet | None = None) -> dict[int, BusinessEntry]:
    if not isinstance(raw, dict):
        raise ValueError("businesses는 객체여야 합니다.")

    businesses: dict[int, BusinessEntry] = {}
    for key, value in raw.items():
        try:
            business_no = int(key)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"사업번호 '{key}'는 정수여야 합니다.") from exc

        role = None
        if isinstance(value, str):
            name = value
        elif isinstance(value, dict):
            name = value.get("name")
            if not name:
                raise ValueError(f"사업 {business_no}에 name이 필요합니다.")
            if value.get("role") is not None:
                role = normalize_role(value.get("role"))
                if role is None:
                    raise ValueError(
                        f"사업 {business_no}의 role '{value.get('role')}'을 알 수 없습니다. "
                        f"쓸 수 있는 값: {', '.join(ROLE_BY_LABEL)}"
                    )
        else:
            raise ValueError(f"사업 {business_no} 설정은 문자열 또는 객체여야 합니다.")

        businesses[business_no] = BusinessEntry(
            name=str(name),
            role=role or infer_role_from_name(str(name), business_no, rules),
        )

    return businesses


def parse_semester_config_data(data: dict[str, Any]) -> SemesterConfig:
    if not isinstance(data, dict):
        raise ValueError("학기 설정은 JSON 객체여야 합니다.")

    label = str(data.get("label", DEFAULT_LABEL)).strip() or DEFAULT_LABEL
    ledger_sheet = str(data.get("ledger_sheet", DEFAULT_LEDGER_SHEET)).strip() or DEFAULT_LEDGER_SHEET

    locker_block = data.get("locker") or {}
    rental_block = data.get("rental") or {}

    businesses_raw = data.get("businesses")
    if businesses_raw is None and "names" in data:
        # {"names": {"1": "..."}} 단축 형식
        businesses_raw = {
            k: {"name": v, "role": LEGACY_ROLE_BY_NUMBER.get(str(k), "general")}
            for k, v in data["names"].items()
        }
    if businesses_raw is None:
        raise ValueError("businesses(또는 names)가 필요합니다.")

    overdue_raw = data.get("overdue_rules")
    if overdue_raw is None:
        raise ValueError("overdue_rules가 필요합니다.")

    rules_user = dict(data.get("비고규칙") or {})
    if locker_block.get("refund_fee_advisory") and "환불수수료_안내" not in rules_user:
        rules_user["환불수수료_안내"] = locker_block["refund_fee_advisory"]
    rules = build_rules(rules_user)

    author_raw = data.get("author") or {}
    author = {
        str(k).strip(): str(v).strip()
        for k, v in author_raw.items()
        if v is not None and str(v).strip() and str(k).strip() in AUTHOR_FIELDS
    } if isinstance(author_raw, dict) else {}

    return SemesterConfig(
        label=label,
        ledger_sheet=ledger_sheet,
        author=author,
        rules=rules,
        businesses=_parse_businesses(businesses_raw, rules),
        locker_deposit_remark=str(
            locker_block.get("deposit_remark", DEFAULT_LOCKER_DEPOSIT_REMARK)
        ),
        locker_refund_advisory=str(
            locker_block.get("refund_fee_advisory", DEFAULT_LOCKER_REFUND_ADVISORY)
        ),
        film_fee_per_sheet=int(
            rental_block.get("film_fee_per_sheet", DEFAULT_FILM_FEE_PER_SHEET)
        ),
        overdue_rules=parse_overdue_rules_data(overdue_raw),
    )


def parse_semester_config_json(text: str) -> SemesterConfig:
    return parse_semester_config_data(json.loads(text))


def load_semester_config(path: Path) -> SemesterConfig:
    with path.open(encoding="utf-8") as f:
        data = json.load(f)
    return parse_semester_config_data(data)


def default_semester_config() -> SemesterConfig:
    """config/학기설정.json → examples/학기설정_예시.json 순으로 로드."""
    root = Path(__file__).resolve().parent.parent
    for relative in ("config/학기설정.json",):
        path = root / relative
        if path.exists():
            return load_semester_config(path)
    raise FileNotFoundError(
        "학기 설정 파일이 없습니다. config/학기설정.json을 준비해 주세요."
    )


def merge_business_names(
    config: SemesterConfig,
    name_overrides: dict[str, str],
) -> SemesterConfig:
    """사업개요에서 파싱한 이름으로 덮어쓴 새 설정을 반환."""
    if not name_overrides:
        return config

    businesses = dict(config.businesses)
    for key, name in name_overrides.items():
        try:
            business_no = int(key)
        except ValueError:
            continue
        businesses[business_no] = BusinessEntry(
            name=name,
            role=infer_role_from_name(name, business_no, config.rules),
        )
    return replace(config, businesses=businesses)


def semester_config_to_dict(config: SemesterConfig) -> dict[str, Any]:
    """zip 보관·미리보기용 직렬화."""
    return {
        "label": config.label,
        "ledger_sheet": config.ledger_sheet,
        "businesses": {
            str(no): {"name": entry.name, "role": entry.role}
            for no, entry in sorted(config.businesses.items())
        },
        "locker": {
            "deposit_remark": config.locker_deposit_remark,
            "refund_fee_advisory": config.locker_refund_advisory,
        },
        "rental": {"film_fee_per_sheet": config.film_fee_per_sheet},
        "author": dict(config.author),
        "비고규칙": config.rules.source,
        "overdue_rules": {
            "default_per_day": config.overdue_rules.default_per_day,
            "categories": [
                {"per_day": c.per_day, "keywords": c.keywords}
                for c in config.overdue_rules.categories
            ],
        },
    }
