"""업로드 양식·사업개요 처리."""
from __future__ import annotations

import re
from dataclasses import dataclass
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


@dataclass
class UploadConfig:
    semester: SemesterConfig
    business_overview: str
    overdue_rules_image_bytes: bytes
    overdue_rules_image_name: str
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


def build_semester_from_overview(
    overview_text: str,
    overdue_rules: OverdueRulesConfig | None = None,
) -> SemesterConfig:
    """사업개요 + (이미지에서 읽은) 연체료 규칙으로 엔진 설정."""
    name_map = parse_business_name_overrides(overview_text)
    config = semester_config_from_business_names(name_map)
    if overdue_rules is None:
        return config
    return SemesterConfig(
        label=config.label,
        ledger_sheet=config.ledger_sheet,
        businesses=config.businesses,
        locker_deposit_remark=config.locker_deposit_remark,
        locker_refund_advisory=config.locker_refund_advisory,
        film_fee_per_sheet=config.film_fee_per_sheet,
        overdue_rules=overdue_rules,
    )


def validate_image(filename: str, data: bytes) -> str:
    ext = Path(filename).suffix.lower()
    if ext not in IMAGE_EXTENSIONS:
        raise ValueError(
            f"연체료 규정은 이미지 파일({', '.join(sorted(IMAGE_EXTENSIONS))})이어야 합니다."
        )
    if len(data) < 8:
        raise ValueError("연체료 규정 이미지가 비어 있습니다.")
    return ext
