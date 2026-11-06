"""업로드 양식·사업개요 처리."""
from __future__ import annotations

import re
from dataclasses import dataclass, replace
from pathlib import Path

from src.config_gen import parse_business_mapping_json
from src.semester_config import (
    DEFAULT_FILM_FEE_PER_SHEET,
    DEFAULT_LABEL,
    DEFAULT_LEDGER_SHEET,
    DEFAULT_LOCKER_DEPOSIT_REMARK,
    DEFAULT_LOCKER_REFUND_ADVISORY,
    SemesterConfig,
    semester_config_from_business_names,
)
from src.config_gen import OverdueRulesConfig
from src.rules_config import ROLE_BY_LABEL, RuleSet

TEMPLATE_EXTENSIONS = {".hwp", ".hwpx"}

_LINE_MAPPING = re.compile(r"^\s*(\d+)\s*[:\.\|]\s*(.+?)\s*$")


# 기본값 — 처음 쓰는 사람이 형식을 따라 적을 수 있게 미리 채운다(학복위 통합본 형식). 학기는 비워 둔다.
EXAMPLE_SEMESTER = ""
EXAMPLE_AUTHOR = {"소속": "학생복지위원회", "직위": "학생복지위원장"}
EXAMPLE_OVERVIEW = """0. 총학생회비 배분
1. 1학기 온라인 사물함 배부 사업
2. 1학기 대면 사물함 배부 사업
3. 1학기 물품 대여 사업
4. 봄 축제
5. 중간고사 간식행사
6. 시험기간 인스타 스토리 인증 이벤트
7. 대동제
8. 과일 화채 행사
9. 중앙도서관 사물함 관리 운영 [일반]
10. 기말고사 간식행사
98. 관리운영비
99. 기타"""
EXAMPLE_OVERDUE_RULES = (
    "1DAY ₩1,000: 보조배터리\n"
    "1DAY ₩3,000: 폴라로이드, 앰프, 듀라테이블, 의자, 카트, 삼각대, 돗자리, 피크닉 테이블, 보드게임, 캐노피, 빔프로젝터\n"
    "1DAY ₩2,000: 그 외 물품"
)


def default_overdue_rules_placeholder() -> str:
    """폼 기본값 — 연체료 규정."""
    return EXAMPLE_OVERDUE_RULES


@dataclass
class UploadConfig:
    semester: SemesterConfig
    business_overview: str
    overdue_rules_text: str
    template_hwpx_path: Path
    overdue_rules_summary: str = ""
    settings_json: str = ""  # 결과 zip에 넣을 설정.json (다음에 다시 불러오기용)
    rental_evidence: object | None = "default"  # 물품 대여 증빙 그림: "default"=기본 그림, None=넣지 않음, EvidenceImage


def default_business_overview_placeholder() -> str:
    """폼 기본값 — 사업 목록."""
    return EXAMPLE_OVERVIEW


def resolve_template(
    template_bytes: bytes,
    filename: str,
    work_dir: Path,
) -> Path:
    """업로드된 .hwp/.hwpx를 fill_hwpx가 쓸 .hwpx 경로로 준비."""
    ext = Path(filename).suffix.lower()
    if ext not in TEMPLATE_EXTENSIONS:
        raise ValueError("수입지출 양식은 .hwp 또는 .hwpx 파일이어야 합니다.")

    work_dir.mkdir(parents=True, exist_ok=True)
    upload_path = work_dir / f"template{ext}"
    upload_path.write_bytes(template_bytes)

    if ext == ".hwpx":
        if template_bytes.startswith(b"\xd0\xcf\x11\xe0"):
            raise ValueError(
                "파일 이름은 .hwpx인데 내용은 예전 한글 형식(.hwp)입니다. 확장자만 바꾸면 hwpx가 되지 않습니다. "
                "한글에서 양식을 열고 '파일 → 다른 이름으로 저장'에서 파일 형식을 'HWPX 문서(*.hwpx)'로 골라 저장한 뒤 올려 주세요."
            )
        if not template_bytes.startswith(b"PK"):
            raise ValueError(
                "올바른 hwpx 형식이 아닙니다. 한글에서 '다른 이름으로 저장 → HWPX 문서(*.hwpx)'로 저장한 파일을 올려 주세요."
            )
        return upload_path

    raise ValueError(
        "서버에서는 .hwp 양식을 읽지 못합니다. 한글에서 양식을 열고 '파일 → 다른 이름으로 저장'에서 "
        "파일 형식을 'HWPX 문서(*.hwpx)'로 골라 저장한 뒤 올려 주세요."
    )


_ROLE_TAG = re.compile(r"\s*[\[［]\s*(" + "|".join(ROLE_BY_LABEL) + r")\s*[\]］]\s*$")


def parse_business_overview(text: str) -> tuple[dict[str, str], dict[str, str]]:
    """사업개요 → (사업명, 직접 지정한 사업 종류).

    `1. 사물함 배부 사업 [사물함]` 처럼 줄 끝에 [사물함]/[대여]/[학생회비]/[일반]을
    붙이면 그 종류를 쓰고, 없으면 사업명 키워드로 추론한다.
    """
    stripped = text.strip()
    if not stripped:
        return {}, {}

    if stripped.startswith("{"):
        return parse_business_mapping_json(stripped), {}

    names: dict[str, str] = {}
    roles: dict[str, str] = {}
    for line in stripped.splitlines():
        match = _LINE_MAPPING.match(line)
        if not match:
            continue
        no, name = match.group(1), match.group(2).strip()
        tag = _ROLE_TAG.search(name)
        if tag:
            roles[no] = ROLE_BY_LABEL[tag.group(1)]
            name = name[: tag.start()].strip()
        names[no] = name
    return names, roles


def parse_business_name_overrides(text: str) -> dict[str, str]:
    """사업개요에서 사업명 매핑 추출 (종류 표시는 떼어 낸다)."""
    return parse_business_overview(text)[0]


@dataclass
class BasicInfo:
    """웹 폼 '기본 정보' — 비워 두면 기존 기본값을 쓴다."""

    semester_label: str = ""
    author: dict[str, str] | None = None
    locker_deposit_remark: str = ""
    film_fee_per_sheet: str = ""


def parse_film_fee(text: str) -> int | None:
    cleaned = re.sub(r"[,\s원₩]", "", text or "")
    if not cleaned:
        return None
    if not cleaned.isdigit() or int(cleaned) <= 0:
        raise ValueError("필름 1장 가격은 0보다 큰 숫자로 입력해 주세요. (예: 1000)")
    return int(cleaned)


def build_semester_from_overview(
    overview_text: str,
    overdue_rules: OverdueRulesConfig | None = None,
    basic: BasicInfo | None = None,
    rules: RuleSet | None = None,
) -> SemesterConfig:
    """사업개요 + 연체료 규칙 + 기본 정보 + 비고 규칙으로 엔진 설정."""
    name_map, roles = parse_business_overview(overview_text)
    config = semester_config_from_business_names(name_map, roles, rules)
    changes: dict = {}
    if overdue_rules is not None:
        changes["overdue_rules"] = overdue_rules
    if basic is not None:
        label = basic.semester_label.strip()
        if label:
            if re.search(r'[\\/:*?"<>|]', label):
                raise ValueError('학기명에는 \\ / : * ? " < > | 를 쓸 수 없습니다.')
            changes["label"] = f"{label}_{DEFAULT_LABEL}"
        author = {k: v.strip() for k, v in (basic.author or {}).items() if v and v.strip()}
        if author:
            changes["author"] = author
        remark = basic.locker_deposit_remark.strip()
        if remark:
            changes["locker_deposit_remark"] = remark
        film_fee = parse_film_fee(basic.film_fee_per_sheet)
        if film_fee is not None:
            changes["film_fee_per_sheet"] = film_fee
    return replace(config, **changes) if changes else config


def validate_image(filename: str, data: bytes) -> str:
    ext = Path(filename).suffix.lower()
    if ext not in IMAGE_EXTENSIONS:
        raise ValueError(
            f"연체료 규정은 이미지 파일({', '.join(sorted(IMAGE_EXTENSIONS))})이어야 합니다."
        )
    if len(data) < 8:
        raise ValueError("연체료 규정 이미지가 비어 있습니다.")
    return ext
