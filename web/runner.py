"""엔진 파이프라인 실행 (web/CLI 공용).

두 단계로 나뉜다.
1) prepare_run: 양식 칸 확인 → 엑셀 읽기 → 페이지·비고 만들기 (미리보기에 쓰는 결과)
2) build_run:   미리보기에서 고친 내용 반영 → hwpx·검수목록·설정.json을 zip으로
"""
from __future__ import annotations

import json
import uuid
import zipfile
from dataclasses import dataclass, field, replace
from io import BytesIO
from pathlib import Path
from typing import BinaryIO

import openpyxl

from src.export_hwp import HwpConversionError, convert_hwpx_to_hwp
from src.fill_hwpx import missing_label_fields
from src.parse_ledger import load_ledger_workbook, parse_ledger_workbook
from src.pipeline import (
    EngineResult,
    ReviewItem,
    export_hwpx_files,
    format_review_report,
    run_engine,
)
from src.remarks import parse_overdue_sheet, parse_refund_sheet
from src.semester_config import semester_config_to_dict
from src.sheet_reader import InputIssue
from src.template_layout import TemplateLayout, analyze_template
from web.errors import run_stage
from web.semester_inputs import UploadConfig

EDITABLE_FIELDS = ("구분", "내용", "비고")
_FIELD_ATTR = {"구분": "classification", "내용": "content", "비고": "remark"}


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
    input_issues: list[InputIssue] = field(default_factory=list)


@dataclass
class PreparedRun:
    """미리보기 단계 결과. edits는 {(사업번호, 페이지번호): {"비고": "...", ...}}."""

    work_dir: Path
    upload: UploadConfig
    layout: TemplateLayout
    result: EngineResult
    input_issues: list[InputIssue]
    edits: dict[tuple[int, int], dict[str, str]] = field(default_factory=dict)

    @property
    def page_count(self) -> int:
        return sum(len(b.pages) for b in self.result.businesses.values())

    def original(self, business_no: int, page_index: int, name: str) -> str:
        page = self.result.businesses[business_no].pages[page_index - 1]
        return getattr(page, _FIELD_ATTR[name])

    def current(self, business_no: int, page_index: int, name: str) -> str:
        edited = self.edits.get((business_no, page_index), {})
        return edited.get(name, self.original(business_no, page_index, name))

    def set_edits(self, values: dict[tuple[int, int, str], str]) -> int:
        """폼에서 받은 값으로 수정 내용을 정한다. 원래 값과 같으면 수정으로 치지 않는다. 수정한 칸 수를 돌려준다."""
        edits: dict[tuple[int, int], dict[str, str]] = {}
        for (biz, idx, name), value in values.items():
            if name not in EDITABLE_FIELDS:
                continue
            business = self.result.businesses.get(biz)
            if business is None or not 1 <= idx <= len(business.pages):
                continue
            value = value.replace("\r\n", "\n").strip()
            if value != self.original(biz, idx, name).strip():
                edits.setdefault((biz, idx), {})[name] = value
        self.edits = edits
        return sum(len(v) for v in edits.values())

    def edited_result(self) -> EngineResult:
        businesses = {}
        for no, biz in self.result.businesses.items():
            pages = []
            for idx, page in enumerate(biz.pages, start=1):
                changes = {_FIELD_ATTR[k]: v for k, v in self.edits.get((no, idx), {}).items()}
                pages.append(replace(page, **changes) if changes else page)
            businesses[no] = replace(biz, pages=pages)
        return EngineResult(businesses=businesses, review_items=self.result.review_items)

    def edit_log(self) -> list[InputIssue]:
        """직접 고친 칸은 감사 기록으로 검수목록에 남긴다."""
        lines = []
        for (no, idx), fields in sorted(self.edits.items()):
            for name in EDITABLE_FIELDS:
                if name in fields:
                    lines.append(InputIssue(
                        "직접수정", f"사업{no} / {idx}페이지 '{name}'을(를) 미리보기에서 고쳤습니다.", advisory=True))
        return lines


def prepare_run(
    ledger_source: str | bytes | BinaryIO,
    overdue_source: str | bytes | BinaryIO,
    refund_source: str | bytes | BinaryIO,
    *,
    upload: UploadConfig,
    work_dir: Path,
) -> PreparedRun:
    work_dir.mkdir(parents=True, exist_ok=True)
    semester = upload.semester
    input_issues: list[InputIssue] = []

    layout = run_stage("수입지출 양식 확인", lambda: analyze_template(str(upload.template_hwpx_path)))
    for note in layout.notes:
        input_issues.append(InputIssue("양식", note, advisory=True))
    if not layout.uses_legacy:
        input_issues.append(InputIssue(
            "양식", f"기존과 다른 양식이라 라벨로 칸을 찾았습니다. 한 페이지에 세목 {layout.rows_per_page}줄씩 넣습니다. "
                   "결과 문서를 한 번 열어 칸이 맞는지 확인해 주세요.", advisory=True))

    def _load_overdue() -> list:
        overdue_wb = openpyxl.load_workbook(overdue_source, read_only=True, data_only=True)
        try:
            return parse_overdue_sheet(overdue_wb.active, issues=input_issues)
        finally:
            overdue_wb.close()

    overdue_records = run_stage("연체료 목록 읽기", _load_overdue)

    def _load_refund() -> list:
        src = BytesIO(refund_source) if isinstance(refund_source, bytes) else refund_source
        refund_wb = openpyxl.load_workbook(src, data_only=True)
        try:
            return parse_refund_sheet(refund_wb.active, issues=input_issues)
        finally:
            refund_wb.close()

    refund_records = run_stage("환불자 목록 읽기", _load_refund)

    def _load_ledger() -> dict:
        ledger_wb = load_ledger_workbook(ledger_source)
        try:
            return parse_ledger_workbook(ledger_wb, semester=semester, issues=input_issues)
        finally:
            ledger_wb.close()

    ledger_by_business = run_stage("장부 읽기", _load_ledger)

    for no in sorted(ledger_by_business):
        if not semester.name_for(no):
            input_issues.append(InputIssue(
                "사업개요", f"장부에 사업번호 {no}번이 있는데 사업개요에 없어 사업명이 비었습니다. "
                           f"사업개요에 '{no}. 사업명' 줄을 추가해 주세요."))
    unused = sorted(set(semester.businesses) - set(ledger_by_business))
    if unused:
        input_issues.append(InputIssue(
            "사업개요", f"사업개요의 {', '.join(f'{n}번' for n in unused)}은(는) 장부에 거래가 없어 문서를 만들지 않았습니다.",
            advisory=True))

    result = run_stage(
        "수입지출내역서 데이터 생성",
        lambda: run_engine(
            ledger_by_business=ledger_by_business,
            semester=semester,
            overdue_records=overdue_records,
            refund_records=refund_records,
            max_rows_per_page=layout.rows_per_page,
        ),
    )

    if semester.author:
        missing = run_stage(
            "양식 작성자 칸 확인",
            lambda: missing_label_fields(str(upload.template_hwpx_path), list(semester.author), layout),
        )
        for name in missing:
            input_issues.append(InputIssue(
                "양식", f"'{name}' 라벨 칸을 찾지 못해 입력한 {name}을(를) 넣지 않았습니다.", advisory=True))

    evidence = _rental_evidence(upload)
    evidence_pages = sum(1 for b in result.businesses.values() for p in b.pages if p.evidence_image)
    if evidence is not None and evidence_pages:
        input_issues.append(InputIssue(
            "증빙서류", f"물품 대여 사업 수입 쪽 {evidence_pages}쪽의 증빙서류 칸에 {evidence.label}를 넣었습니다. "
                       "이번 학기 연체료 규정과 같은 그림인지 확인해 주세요.", advisory=True, auto=True))

    return PreparedRun(
        work_dir=work_dir, upload=upload, layout=layout, result=result, input_issues=input_issues,
    )


def _rental_evidence(upload: UploadConfig):
    from src.evidence_image import default_rental_evidence

    if upload.rental_evidence == "default":
        return default_rental_evidence()
    return upload.rental_evidence


def build_run(prep: PreparedRun) -> PipelineResult:
    """미리보기 수정 내용을 반영해 결과 zip을 만든다. 여러 번 불러도 된다(매번 새 zip)."""
    upload = prep.upload
    semester = upload.semester
    result = prep.edited_result()
    build_dir = prep.work_dir / f"build_{uuid.uuid4().hex[:8]}"
    output_dir = build_dir / "out"
    output_dir.mkdir(parents=True, exist_ok=True)
    issues = list(prep.input_issues)

    problems: list[str] = []
    hwpx_paths = run_stage(
        "hwpx 파일 생성",
        lambda: export_hwpx_files(
            result,
            template_path=str(upload.template_hwpx_path),
            output_dir=str(output_dir),
            filename_prefix=semester.label,
            author=semester.author,
            layout=prep.layout,
            problems=problems,
            evidence_image=_rental_evidence(upload),
        ),
    )
    issues += [InputIssue("양식", p) for p in problems]
    issues += prep.edit_log()

    review_text = format_review_report(result.review_items, issues)
    rules_extract = json.dumps(
        semester_config_to_dict(semester)["overdue_rules"], ensure_ascii=False, indent=2
    )

    hwp_paths: list[str] = []
    hwp_conversion_note: str | None = None
    try:
        hwp_paths = convert_hwpx_to_hwp(hwpx_paths)
    except HwpConversionError as exc:
        hwp_conversion_note = str(exc)
    zip_doc_paths = hwp_paths if hwp_paths else hwpx_paths

    zip_path = build_dir / "수입지출내역서_결과.zip"

    def _build_zip() -> None:
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("검수목록.txt", review_text)
            zf.writestr("연체료규정_추출.json", rules_extract)
            if upload.business_overview.strip():
                zf.writestr("사업개요.txt", upload.business_overview)
            zf.writestr("연체료규정.txt", upload.overdue_rules_text)
            if upload.settings_json:
                zf.writestr("설정.json", upload.settings_json)
            for hp in zip_doc_paths:
                zf.write(hp, Path(hp).name)

    run_stage("결과 zip 패키징", _build_zip)

    for hp in [*hwpx_paths, *hwp_paths]:
        Path(hp).unlink(missing_ok=True)
    try:
        output_dir.rmdir()
    except OSError:
        pass

    summary = PipelineSummary(
        business_count=len(result.businesses),
        page_count=prep.page_count,
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
        input_issues=issues,
    )


def run_pipeline_from_uploads(
    ledger_source: str | bytes | BinaryIO,
    overdue_source: str | bytes | BinaryIO,
    refund_source: str | bytes | BinaryIO,
    *,
    upload: UploadConfig,
    work_dir: Path,
) -> PipelineResult:
    """미리보기 없이 한 번에 결과 zip까지 만든다."""
    prep = prepare_run(ledger_source, overdue_source, refund_source, upload=upload, work_dir=work_dir)
    return build_run(prep)
