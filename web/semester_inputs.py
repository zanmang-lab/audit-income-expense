"""업로드 양식·사업개요 처리."""
from __future__ import annotations

import re
from dataclasses import dataclass, replace
from pathlib import Path

from src.config_gen import parse_business_mapping_json
from src.export_hwp import HwpConversionError, convert_hwp_to_hwpx, hwp_conversion_available
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

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".svg"}
TEMPLATE_EXTENSIONS = {".hwp", ".hwpx"}

_LINE_MAPPING = re.compile(r"^\s*(\d+)\s*[:\.\|]\s*(.+?)\s*$")


def default_overdue_rules_placeholder() -> str:
    """폼 기본값 — 연체료 규정 입력 예시."""
    return (
        "1DAY ₩1,000: 보조배터리\n"
        "1DAY ₩2,000: 그 외 물품\n"
        "1DAY ₩3,000: 폴라로이드, 앰프, 듀라테이블, 의자, 카트, 피크닉용품, 캐노피, 빔프로젝터"
    )


@dataclass
class UploadConfig:
    semester: SemesterConfig
    business_overview: str
    overdue_rules_text: str
    template_hwpx_path: Path
    overdue_rules_summary: str = ""


def default_business_overview_placeholder() -> str:
    """폼 기본값 — 업로드 양식 안내용 예시 (서버 설정과 무관)."""
    return (
        "0. 총학생회비\n"
        "1. 사물함 배부 사업\n"
        "2. 물품 대여 사업"
    )


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
        if not template_bytes.startswith(b"PK"):
            raise ValueError("올바른 hwpx 형식이 아닙니다.")
        return upload_path

    if not hwp_conversion_available():
        raise ValueError(
            "온라인 서버에서는 hwpx 양식만 업로드할 수 있습니다. "
            "한글에서 '다른 이름으로 저장 → hwpx' 후 다시 업로드해 주세요."
        )

    hwpx_path = work_dir / "template.hwpx"
    try:
        return Path(convert_hwp_to_hwpx(str(upload_path), str(hwpx_path)))
    except HwpConversionError as exc:
        raise ValueError(
            "hwp 양식을 hwpx로 변환하지 못했습니다. "
            "한글이 설치된 Windows 서버에서 실행하거나, "
            "한글에서 hwpx로 저장한 파일을 업로드해 주세요."
        ) from exc


def parse_business_name_overrides(text: str) -> dict[str, str]:
    """사업개요에서 사업명 매핑 추출."""
    stripped = text.strip()
    if not stripped:
        return {}

    if stripped.startswith("{"):
        return parse_business_mapping_json(stripped)

    mapping: dict[str, str] = {}
    for line in stripped.splitlines():
        match = _LINE_MAPPING.match(line)
        if match:
            mapping[match.group(1)] = match.group(2).strip()
    return mapping


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
) -> SemesterConfig:
    """사업개요 + 연체료 규칙 + 기본 정보로 엔진 설정."""
    name_map = parse_business_name_overrides(overview_text)
    config = semester_config_from_business_names(name_map)
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
