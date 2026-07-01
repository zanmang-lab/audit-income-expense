"""FastAPI 웹 앱 — 업로드 → 마스킹 → 엔진 → zip 다운로드."""
from __future__ import annotations

import logging
import shutil
import sys
import time
import uuid
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import BaseHTTPMiddleware

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


from src.parse_overdue_rules_image import (
    PARSER_BUILD_ID,
    parse_overdue_rules_image,
    rules_to_summary,
)
from src.export_hwp import hwp_conversion_available
from web.errors import PipelineError, format_error_detail, run_stage
from web.masking import mask_refund_workbook
from web.runner import PipelineResult, run_pipeline_from_uploads
from web.form_examples import upload_form_examples_html
from web.sample_files import ensure_example_files, resolve_example_path
from web.semester_inputs import (
    UploadConfig,
    build_semester_from_overview,
    default_business_overview_placeholder,
    resolve_template,
    validate_image,
)

TEMP_ROOT = PROJECT_ROOT / "web" / "_tmp"
TEMP_ROOT.mkdir(parents=True, exist_ok=True)
EXAMPLES_DIR = PROJECT_ROOT / "examples"
TEMPLATES_DIR = PROJECT_ROOT / "web" / "templates"

MAX_FILE_BYTES = 15 * 1024 * 1024
JOB_TTL_SECONDS = 600

app = FastAPI(title="수입지출내역서 자동 작성")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
logger = logging.getLogger(__name__)


class NoCacheHtmlMiddleware(BaseHTTPMiddleware):
    """브라우저가 예전 HTML을 캐시하지 않도록 한다."""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        content_type = response.headers.get("content-type", "")
        if "text/html" in content_type:
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
            response.headers["Pragma"] = "no-cache"
        return response


app.add_middleware(NoCacheHtmlMiddleware)


@dataclass
class DownloadJob:
    zip_path: Path
    work_dir: Path
    created_at: float
    masking_ok: bool
    masking_message: str
    business_count: int
    page_count: int
    hwp_count: int
    output_format: str
    hwp_conversion_note: str | None
    required_count: int
    advisory_count: int
    rules_summary: str
    review_required: list[str]
    review_advisory: list[str]


_jobs: dict[str, DownloadJob] = {}


def _cleanup_expired_jobs() -> None:
    now = time.time()
    expired = [jid for jid, job in _jobs.items() if now - job.created_at > JOB_TTL_SECONDS]
    for jid in expired:
        _remove_job(jid)


def _remove_job(job_id: str) -> None:
    job = _jobs.pop(job_id, None)
    if not job:
        return
    if job.zip_path.exists():
        job.zip_path.unlink(missing_ok=True)
    if job.work_dir.exists():
        shutil.rmtree(job.work_dir, ignore_errors=True)


def _wants_html(request: Request) -> bool:
    accept = request.headers.get("accept", "")
    return "text/html" in accept or "*/*" in accept or not accept


def _error_page_context(detail: str, status_code: int) -> dict:
    detail_str = str(detail)
    show_ocr = "연체료 규정" in detail_str or "이미지" in detail_str
    if show_ocr:
        title = "연체료 규정 이미지를 읽지 못했습니다"
        icon = "document_scanner"
    elif "xlsx" in detail_str.lower() or "엑셀" in detail_str:
        title = "엑셀 파일을 읽지 못했습니다"
        icon = "table_view"
    elif "양식" in detail_str or "hwp" in detail_str.lower():
        title = "수입지출 양식을 준비하지 못했습니다"
        icon = "description"
    elif status_code >= 500:
        title = "문서 생성 중 오류가 발생했습니다"
        icon = "error"
    else:
        title = "요청을 처리하지 못했습니다"
        icon = "warning"
    return {
        "error_title": title,
        "error_message": detail_str,
        "error_icon": icon,
        "show_ocr_tips": show_ocr,
    }


def _render_error(request: Request, detail: str, status_code: int) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "error.html",
        _error_page_context(detail, status_code),
        status_code=status_code,
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException) -> HTMLResponse:
    if _wants_html(request):
        return _render_error(request, str(exc.detail), exc.status_code)
    return HTMLResponse(content=str(exc.detail), status_code=exc.status_code)


async def _read_xlsx(upload: UploadFile, field_name: str) -> bytes:
    if not upload.filename or not upload.filename.lower().endswith(".xlsx"):
        raise HTTPException(400, f"{field_name}: .xlsx 파일만 업로드할 수 있습니다.")
    data = await upload.read()
    if len(data) > MAX_FILE_BYTES:
        raise HTTPException(400, f"{field_name}: 파일 크기는 15MB 이하여야 합니다.")
    if not data.startswith(b"PK"):
        raise HTTPException(400, f"{field_name}: 올바른 xlsx 형식이 아닙니다.")
    return data


async def _read_bytes(upload: UploadFile, field_name: str, max_bytes: int = MAX_FILE_BYTES) -> bytes:
    data = await upload.read()
    if not data:
        raise HTTPException(400, f"{field_name}: 파일이 비어 있습니다.")
    if len(data) > max_bytes:
        raise HTTPException(400, f"{field_name}: 파일 크기는 {max_bytes // (1024 * 1024)}MB 이하여야 합니다.")
    return data


def _ocr_engine_ready() -> bool:
    try:
        from src.parse_overdue_rules_image import _get_rapid_ocr

        _get_rapid_ocr()
        return True
    except Exception:
        return False


@app.on_event("startup")
async def startup_prepare_examples() -> None:
    ensure_example_files(EXAMPLES_DIR)
    if _ocr_engine_ready():
        logger.info("OCR 엔진 준비 완료")
    else:
        logger.warning("OCR 엔진을 로드하지 못했습니다. 연체료 이미지 인식이 실패할 수 있습니다.")


@app.get("/api/health")
def health_check():
    """실행 중인 서버가 최신 연체료 파서를 쓰는지 확인."""
    import os

    return {
        "status": "ok",
        "parser_build": PARSER_BUILD_ID,
        "supports_table_format": True,
        "app": "upload-web",
        "hwp_supported": hwp_conversion_available(),
        "ocr_available": _ocr_engine_ready(),
        "public_url": os.environ.get("RENDER_EXTERNAL_URL"),
    }


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    _cleanup_expired_jobs()
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "default_overview": default_business_overview_placeholder(),
            "examples": upload_form_examples_html(),
            "hwp_supported": hwp_conversion_available(),
        },
    )


@app.post("/generate", response_class=HTMLResponse)
async def generate(
    request: Request,
    business_overview: str = Form(...),
    ledger: UploadFile = File(...),
    overdue: UploadFile = File(...),
    refund: UploadFile = File(...),
    template: UploadFile = File(...),
    overdue_rules_image: UploadFile = File(...),
) -> HTMLResponse:
    _cleanup_expired_jobs()

    ledger_bytes = await _read_xlsx(ledger, "장부")
    overdue_bytes = await _read_xlsx(overdue, "연체료목록")
    refund_bytes = await _read_xlsx(refund, "환불자목록")

    if not template.filename:
        raise HTTPException(400, "수입지출 양식 파일을 업로드해 주세요.")
    template_bytes = await _read_bytes(template, "수입지출양식")

    if not overdue_rules_image.filename:
        raise HTTPException(400, "연체료 규정 이미지를 업로드해 주세요.")
    rules_image_bytes = await _read_bytes(
        overdue_rules_image, "연체료규정이미지", max_bytes=10 * 1024 * 1024
    )

    try:
        image_ext = validate_image(overdue_rules_image.filename, rules_image_bytes)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    try:
        overdue_rules = run_stage(
            "연체료 규정 이미지 인식",
            lambda: parse_overdue_rules_image(rules_image_bytes, overdue_rules_image.filename),
        )
        rules_summary = rules_to_summary(overdue_rules)
    except PipelineError as exc:
        raise HTTPException(400, str(exc)) from exc

    try:
        semester = build_semester_from_overview(business_overview, overdue_rules)
    except ValueError as exc:
        raise HTTPException(400, format_error_detail("사업개요", exc)) from exc

    overview_text = business_overview.strip()

    try:
        masked_refund, dropped_cols = run_stage(
            "환불자 목록 마스킹",
            lambda: mask_refund_workbook(refund_bytes),
        )
    except PipelineError as exc:
        raise HTTPException(500, str(exc)) from exc

    if dropped_cols:
        masking_ok = False
        masking_message = (
            f"민감정보 열 {len(dropped_cols)}개가 감지되어 제거되었습니다: "
            f"[{', '.join(dropped_cols)}]"
        )
    else:
        masking_ok = True
        masking_message = "환불자 목록에 전화번호·계좌번호 등 민감정보 열이 포함되지 않았습니다."

    job_id = uuid.uuid4().hex
    work_dir = TEMP_ROOT / job_id
    work_dir.mkdir(parents=True, exist_ok=True)

    try:
        template_hwpx = run_stage(
            "수입지출 양식 준비",
            lambda: resolve_template(template_bytes, template.filename, work_dir),
        )
    except PipelineError as exc:
        shutil.rmtree(work_dir, ignore_errors=True)
        raise HTTPException(400, str(exc)) from exc

    upload_config = UploadConfig(
        semester=semester,
        business_overview=overview_text,
        overdue_rules_image_bytes=rules_image_bytes,
        overdue_rules_image_name=image_ext,
        template_hwpx_path=template_hwpx,
        overdue_rules_summary=rules_summary,
    )

    try:
        result: PipelineResult = run_pipeline_from_uploads(
            ledger_source=BytesIO(ledger_bytes),
            overdue_source=BytesIO(overdue_bytes),
            refund_source=masked_refund,
            upload=upload_config,
            work_dir=work_dir,
        )
    except PipelineError as exc:
        shutil.rmtree(work_dir, ignore_errors=True)
        logger.exception("파이프라인 오류")
        raise HTTPException(500, str(exc)) from exc
    except Exception as exc:
        shutil.rmtree(work_dir, ignore_errors=True)
        logger.exception("처리 중 예기치 않은 오류")
        raise HTTPException(500, format_error_detail("문서 생성", exc)) from exc

    s = result.summary
    required_items = [item for item in result.review_items if not item.advisory]
    advisory_items = [item for item in result.review_items if item.advisory]

    _jobs[job_id] = DownloadJob(
        zip_path=result.zip_path,
        work_dir=work_dir,
        created_at=time.time(),
        masking_ok=masking_ok,
        masking_message=masking_message,
        business_count=s.business_count,
        page_count=s.page_count,
        hwp_count=s.hwp_count,
        output_format=s.output_format,
        hwp_conversion_note=s.hwp_conversion_note,
        required_count=len(required_items),
        advisory_count=len(advisory_items),
        rules_summary=rules_summary,
        review_required=[item.format_line() for item in required_items],
        review_advisory=[item.format_line() for item in advisory_items],
    )

    job = _jobs[job_id]
    return templates.TemplateResponse(
        request,
        "result.html",
        {
            "job_id": job_id,
            "masking_ok": job.masking_ok,
            "masking_message": job.masking_message,
            "business_count": job.business_count,
            "page_count": job.page_count,
            "hwp_count": job.hwp_count,
            "hwpx_count": s.hwpx_count,
            "output_format": job.output_format,
            "hwp_conversion_note": job.hwp_conversion_note,
            "required_count": job.required_count,
            "advisory_count": job.advisory_count,
            "rules_summary": job.rules_summary,
            "review_required": job.review_required,
            "review_advisory": job.review_advisory,
        },
    )


@app.get("/examples/{filename}")
async def download_example(filename: str) -> FileResponse:
    path = resolve_example_path(EXAMPLES_DIR, filename)
    if not path:
        raise HTTPException(404, "예시 파일을 찾을 수 없습니다.")

    media_types = {
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".txt": "text/plain; charset=utf-8",
        ".svg": "image/svg+xml",
        ".json": "application/json; charset=utf-8",
    }
    media_type = media_types.get(path.suffix.lower(), "application/octet-stream")
    return FileResponse(path=path, filename=path.name, media_type=media_type)


@app.get("/download/{job_id}")
async def download(job_id: str) -> FileResponse:
    _cleanup_expired_jobs()
    job = _jobs.get(job_id)
    if not job or not job.zip_path.exists():
        raise HTTPException(404, "다운로드 링크가 만료되었거나 존재하지 않습니다.")
    return FileResponse(
        path=job.zip_path,
        filename="수입지출내역서_결과.zip",
        media_type="application/zip",
    )


@app.on_event("shutdown")
async def shutdown_cleanup() -> None:
    for jid in list(_jobs.keys()):
        _remove_job(jid)
