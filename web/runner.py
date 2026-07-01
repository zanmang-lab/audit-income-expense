"""엔진 파이프라인 실행 (web/CLI 공용)."""
from __future__ import annotations

import json
import zipfile
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import BinaryIO

import openpyxl

from src.export_hwp import HwpConversionError, convert_hwpx_to_hwp
from src.parse_ledger import load_ledger_workbook, parse_ledger_workbook
from src.pipeline import (
    ReviewItem,
    export_hwpx_files,
    format_review_report,
    result_to_preview_dict,
    run_engine,
)
from src.remarks import parse_overdue_sheet, parse_refund_sheet
from src.semester_config import semester_config_to_dict
from web.errors import run_stage
from web.semester_inputs import UploadConfig


@dataclass
class PipelineSummary:
    business_count: int
    page_count: int
    review_count: int
    hwpx_count: int
    hwp_count: int
    output_format: str
    hwp_conversion_note: str | None = None


@dataclass
class PipelineResult:
    summary: PipelineSummary
    zip_path: Path
    dropped_sensitive_columns: list[str]
    review_items: list[ReviewItem]


def run_pipeline_from_uploads(
    ledger_source: str | bytes | BinaryIO,
    overdue_source: str | bytes | BinaryIO,
    refund_source: str | bytes | BinaryIO,
    *,
    upload: UploadConfig,
    work_dir: Path,
) -> PipelineResult:
    """업로드된 엑셀·설정으로 hwp+검수목록을 생성하고 zip 경로를 반환."""
    work_dir.mkdir(parents=True, exist_ok=True)
    output_dir = work_dir / "out"
    output_dir.mkdir(exist_ok=True)
    semester = upload.semester

    def _load_overdue() -> list:
        overdue_wb = openpyxl.load_workbook(overdue_source, read_only=True, data_only=True)
        try:
            return parse_overdue_sheet(overdue_wb.active)
        finally:
            overdue_wb.close()

    overdue_records = run_stage("연체료 목록 읽기", _load_overdue)

    def _load_refund() -> list:
        refund_wb = openpyxl.load_workbook(BytesIO(refund_source), data_only=True)
        try:
            return parse_refund_sheet(refund_wb.active)
        finally:
            refund_wb.close()

    refund_records = run_stage("환불자 목록 읽기", _load_refund)

    def _load_ledger() -> dict:
        ledger_wb = load_ledger_workbook(ledger_source)
        try:
            return parse_ledger_workbook(ledger_wb, semester=semester)
        finally:
            ledger_wb.close()

    ledger_by_business = run_stage("장부 읽기", _load_ledger)

    result = run_stage(
        "수입지출내역서 데이터 생성",
        lambda: run_engine(
            ledger_by_business=ledger_by_business,
            semester=semester,
            overdue_records=overdue_records,
            refund_records=refund_records,
        ),
    )

    preview_path = output_dir / "preview.json"
    review_path = output_dir / "검수목록.txt"

    rules_extract_path = output_dir / "연체료규정_추출.json"

    def _write_reports() -> None:
        with preview_path.open("w", encoding="utf-8") as f:
            json.dump(result_to_preview_dict(result), f, ensure_ascii=False, indent=2)
        review_path.write_text(format_review_report(result.review_items), encoding="utf-8")
        with rules_extract_path.open("w", encoding="utf-8") as f:
            json.dump(
                semester_config_to_dict(semester)["overdue_rules"],
                f,
                ensure_ascii=False,
                indent=2,
            )

    run_stage("검수목록 작성", _write_reports)

    hwpx_paths = run_stage(
        "hwpx 파일 생성",
        lambda: export_hwpx_files(
            result,
            template_path=str(upload.template_hwpx_path),
            output_dir=str(output_dir),
            filename_prefix=semester.label,
        ),
    )

    hwp_paths: list[str] = []
    hwp_conversion_note: str | None = None
    try:
        hwp_paths = convert_hwpx_to_hwp(hwpx_paths)
    except HwpConversionError as exc:
        hwp_conversion_note = str(exc)

    zip_doc_paths = hwp_paths if hwp_paths else hwpx_paths

    overview_path = output_dir / "사업개요.txt"
    overview_path.write_text(upload.business_overview, encoding="utf-8")

    rules_text_path = output_dir / "연체료규정.txt"
    rules_text_path.write_text(upload.overdue_rules_text, encoding="utf-8")

    zip_path = work_dir / "수입지출내역서_결과.zip"

    def _build_zip() -> None:
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.write(review_path, "검수목록.txt")
            zf.write(rules_extract_path, "연체료규정_추출.json")
            if upload.business_overview.strip():
                zf.write(overview_path, "사업개요.txt")
            zf.write(rules_text_path, "연체료규정.txt")
            for hp in zip_doc_paths:
                zf.write(hp, Path(hp).name)

    run_stage("결과 zip 패키징", _build_zip)

    preview_path.unlink(missing_ok=True)
    rules_extract_path.unlink(missing_ok=True)
    for hp in hwpx_paths:
        Path(hp).unlink(missing_ok=True)
    for hp in hwp_paths:
        Path(hp).unlink(missing_ok=True)
    review_path.unlink(missing_ok=True)
    overview_path.unlink(missing_ok=True)
    rules_text_path.unlink(missing_ok=True)
    try:
        output_dir.rmdir()
    except OSError:
        pass

    total_pages = sum(len(b.pages) for b in result.businesses.values())
    summary = PipelineSummary(
        business_count=len(result.businesses),
        page_count=total_pages,
        review_count=len(result.review_items),
        hwpx_count=len(hwpx_paths),
        hwp_count=len(hwp_paths),
        output_format="hwp" if hwp_paths else "hwpx",
        hwp_conversion_note=hwp_conversion_note,
    )
    return PipelineResult(
        summary=summary,
        zip_path=zip_path,
        dropped_sensitive_columns=[],
        review_items=result.review_items,
    )
