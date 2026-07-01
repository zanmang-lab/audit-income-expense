"""CLI 파이프라인 — input/ 폴더의 엑셀 + config/학기설정.json."""
from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import openpyxl

INPUT_DIR = PROJECT_ROOT / "input"
CONFIG_DIR = PROJECT_ROOT / "config"
OUTPUT_DIR = PROJECT_ROOT / "output"
TEMPLATE_PATH = PROJECT_ROOT / "templates" / "수입지출내역서_양식.hwpx"
SEMESTER_CONFIG_PATH = CONFIG_DIR / "학기설정.json"

# input/ 안 표준 파일명
LEDGER_FILE = "장부.xlsx"
OVERDUE_FILE = "연체료목록.xlsx"
REFUND_FILE = "환불자목록.xlsx"

from src.parse_ledger import load_ledger_workbook, parse_ledger_workbook
from src.pipeline import (
    export_hwpx_files,
    format_review_report,
    result_to_preview_dict,
    run_engine,
)
from src.export_hwp import HwpConversionError, convert_hwpx_to_hwp
from src.remarks import parse_overdue_sheet, parse_refund_sheet
from src.semester_config import default_semester_config, load_semester_config


def _require_input(filename: str) -> Path:
    path = INPUT_DIR / filename
    if not path.exists():
        raise FileNotFoundError(
            f"입력 파일이 없습니다: input/{filename}\n"
            f"장부·연체료·환불 목록을 input/ 폴더에 넣어 주세요."
        )
    return path


def _load_semester_config() -> "SemesterConfig":
    if SEMESTER_CONFIG_PATH.exists():
        return load_semester_config(SEMESTER_CONFIG_PATH)
    return default_semester_config()


def main() -> int:
    from src.semester_config import SemesterConfig

    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    INPUT_DIR.mkdir(parents=True, exist_ok=True)

    semester: SemesterConfig = _load_semester_config()

    ledger_path = _require_input(LEDGER_FILE)
    overdue_path = _require_input(OVERDUE_FILE)
    refund_path = _require_input(REFUND_FILE)

    overdue_wb = openpyxl.load_workbook(overdue_path, read_only=True, data_only=True)
    overdue_records = parse_overdue_sheet(overdue_wb.active)
    overdue_wb.close()

    refund_wb = openpyxl.load_workbook(refund_path, read_only=True, data_only=True)
    refund_records = parse_refund_sheet(refund_wb.active)
    refund_wb.close()

    ledger_wb = load_ledger_workbook(ledger_path)
    ledger_by_business = parse_ledger_workbook(ledger_wb, semester=semester)
    ledger_wb.close()

    result = run_engine(
        ledger_by_business=ledger_by_business,
        semester=semester,
        overdue_records=overdue_records,
        refund_records=refund_records,
    )

    preview_path = OUTPUT_DIR / "preview.json"
    review_path = OUTPUT_DIR / "검수목록.txt"

    with preview_path.open("w", encoding="utf-8") as f:
        json.dump(result_to_preview_dict(result), f, ensure_ascii=False, indent=2)

    review_path.write_text(format_review_report(result.review_items), encoding="utf-8")

    if not TEMPLATE_PATH.exists():
        print(f"오류: 양식 파일 없음 — {TEMPLATE_PATH}", file=sys.stderr)
        return 1

    hwpx_paths = export_hwpx_files(
        result,
        template_path=str(TEMPLATE_PATH),
        output_dir=str(OUTPUT_DIR),
        filename_prefix=semester.label,
    )

    hwp_paths: list[str] = []
    try:
        hwp_paths = convert_hwpx_to_hwp(hwpx_paths)
    except HwpConversionError as exc:
        print(f"[경고] hwp 변환 생략: {exc}", file=sys.stderr)

    total_pages = sum(len(b.pages) for b in result.businesses.values())
    print("── 처리 요약 ──")
    print(f"  학기 설정:      {semester.label}")
    print(f"  처리한 사업 수: {len(result.businesses)}")
    print(f"  총 페이지 수:   {total_pages}")
    print(f"  검수필요 건수:  {len(result.review_items)}")
    print(f"  preview:        {preview_path}")
    print(f"  검수목록:       {review_path}")
    print(f"  hwp 파일:       {len(hwp_paths)}개 → {OUTPUT_DIR}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
