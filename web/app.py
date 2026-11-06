"""FastAPI 웹 앱 — 업로드 → 마스킹 → 엔진 → zip 다운로드."""
from __future__ import annotations

import json

import logging
import shutil
import sys
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import BaseHTTPMiddleware

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


from src.parse_overdue_rules import (
    PARSER_BUILD_ID,
    parse_overdue_rules_text,
    rules_to_summary,
)
from web.errors import PipelineError, format_error_detail, is_user_input_error, run_stage
from web.masking import mask_refund_workbook
from web.preview import build_preview_view, parse_edit_form
from web.runner import PipelineResult, PreparedRun, build_run, prepare_run
from web.form_examples import upload_form_examples_html
from web.sample_files import ensure_example_files, resolve_example_path
from src.rules_config import TEMPLATE_FIELDS, RulesError
from src.semester_config import DEFAULT_FILM_FEE_PER_SHEET, DEFAULT_LOCKER_DEPOSIT_REMARK
from web.settings_file import (
    build_settings,
    default_rules_text,
    example_settings,
    parse_rules_text,
    settings_to_json,
)
from web.semester_inputs import (
    EXAMPLE_AUTHOR,
    BasicInfo,
    UploadConfig,
    build_semester_from_overview,
    default_business_overview_placeholder,
    default_overdue_rules_placeholder,
    resolve_template,
)

TEMP_ROOT = PROJECT_ROOT / "web" / "_tmp"
TEMP_ROOT.mkdir(parents=True, exist_ok=True)
EXAMPLES_DIR = PROJECT_ROOT / "examples"
TEMPLATES_DIR = PROJECT_ROOT / "web" / "templates"

MAX_FILE_BYTES = 15 * 1024 * 1024
JOB_TTL_SECONDS = 600       # 다운로드 링크
PREVIEW_TTL_SECONDS = 1800  # 미리보기에서 고치는 시간

@asynccontextmanager
async def lifespan(_app: FastAPI):
    """서버 시작 시 예시 파일 준비, 종료 시 임시 작업 폴더 정리."""
    ensure_example_files(EXAMPLES_DIR)
    yield
    for jid in list(_jobs.keys()):
        _remove_job(jid)
    for pid in list(_previews.keys()):
        _remove_preview(pid)


app = FastAPI(title="수입지출내역서 자동 작성", lifespan=lifespan)
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
app.mount("/static", StaticFiles(directory=str(Path(__file__).resolve().parent / "static")), name="static")
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
    created_at: float


@dataclass
class PreviewJob:
    """미리보기 단계 작업. 고친 내용은 prep.edits에 남고, 문서는 여러 번 다시 만들 수 있다."""

    prep: PreparedRun
    created_at: float
    masking_ok: bool
    masking_message: str
    rules_summary: str
    builds: list[str] = field(default_factory=list)


_jobs: dict[str, DownloadJob] = {}
_previews: dict[str, PreviewJob] = {}


def _cleanup_expired_jobs() -> None:
    now = time.time()
    for jid in [j for j, job in _jobs.items() if now - job.created_at > JOB_TTL_SECONDS]:
        _remove_job(jid)
    for pid in [p for p, job in _previews.items() if now - job.created_at > PREVIEW_TTL_SECONDS]:
        _remove_preview(pid)


def _remove_job(job_id: str) -> None:
    job = _jobs.pop(job_id, None)
    if job and job.zip_path.exists():
        job.zip_path.unlink(missing_ok=True)


def _remove_preview(preview_id: str) -> None:
    job = _previews.pop(preview_id, None)
    if not job:
        return
    for jid in job.builds:
        _jobs.pop(jid, None)
    shutil.rmtree(job.prep.work_dir, ignore_errors=True)


def _wants_html(request: Request) -> bool:
    accept = request.headers.get("accept", "")
    return "text/html" in accept or "*/*" in accept or not accept


def _error_page_context(detail: str, status_code: int) -> dict:
    detail_str = str(detail)
    if "연체료 규정" in detail_str:
        title = "연체료 규정을 읽지 못했습니다"
        icon = "document_scanner"
    elif "xlsx" in detail_str.lower() or "엑셀" in detail_str or "열을 찾지 못했습니다" in detail_str or "장부 데이터를 찾지 못했습니다" in detail_str:
        title = "엑셀 파일을 읽지 못했습니다"
        icon = "table_view"
    elif "양식" in detail_str or "hwp" in detail_str.lower():
        title = "수입지출 양식을 준비하지 못했습니다"
        icon = "description"
    elif "비고 규칙" in detail_str:
        title = "비고 규칙을 읽지 못했습니다"
        icon = "rule"
    elif "기본 정보" in detail_str or "사업개요" in detail_str:
        title = "입력값을 확인해 주세요"
        icon = "edit_note"
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
    }


def _render_error(request: Request, detail: str, status_code: int) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "error.html",
        _error_page_context(detail, status_code),
        status_code=status_code,
    )


def _is_fetch(request: Request) -> bool:
    return request.headers.get("x-requested-with") == "fetch"


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException) -> HTMLResponse:
    if _is_fetch(request):
        ctx = _error_page_context(str(exc.detail), exc.status_code)
        return JSONResponse({"title": ctx["error_title"], "detail": str(exc.detail)}, status_code=exc.status_code)
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


@app.get("/api/health")
def health_check():
    """실행 중인 서버가 최신 연체료 파서를 쓰는지 확인."""
    import os

    return {
        "status": "ok",
        "parser_build": PARSER_BUILD_ID,
        "supports_table_format": True,
        "app": "upload-web",
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
            "default_overdue_rules": default_overdue_rules_placeholder(),
            "examples": upload_form_examples_html(),
            "default_locker_remark": DEFAULT_LOCKER_DEPOSIT_REMARK,
            "default_film_fee": DEFAULT_FILM_FEE_PER_SHEET,
            "default_rules_json": default_rules_text(),
            "example_author": EXAMPLE_AUTHOR,
            "template_fields": TEMPLATE_FIELDS,
            "admin": "admin" in request.query_params or "관리자" in request.query_params,
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
    rental_evidence: UploadFile | None = File(None),
    overdue_rules_text: str = Form(...),
    semester_label: str = Form(""),
    author_name: str = Form(""),
    author_position: str = Form(""),
    author_affiliation: str = Form(""),
    author_contact: str = Form(""),
    locker_deposit_remark: str = Form(""),
    film_fee_per_sheet: str = Form(""),
    rules_json: str = Form(""),
) -> HTMLResponse:
    _cleanup_expired_jobs()

    ledger_bytes = await _read_xlsx(ledger, "장부")
    overdue_bytes = await _read_xlsx(overdue, "연체료목록")
    refund_bytes = await _read_xlsx(refund, "환불자목록")

    if not template.filename:
        raise HTTPException(400, "수입지출 양식 파일을 업로드해 주세요.")
    template_bytes = await _read_bytes(template, "수입지출양식")

    rules_text = overdue_rules_text.strip()
    if not rules_text:
        raise HTTPException(400, "연체료 규정을 입력해 주세요.")

    try:
        overdue_rules = run_stage(
            "연체료 규정 해석",
            lambda: parse_overdue_rules_text(rules_text),
        )
        rules_summary = rules_to_summary(overdue_rules)
    except PipelineError as exc:
        raise HTTPException(400, str(exc)) from exc

    try:
        basic = BasicInfo(
            semester_label=semester_label,
            author={
                "성명": author_name,
                "직위": author_position,
                "소속": author_affiliation,
                "연락처": author_contact,
            },
            locker_deposit_remark=locker_deposit_remark,
            film_fee_per_sheet=film_fee_per_sheet,
        )
        remark_rules = parse_rules_text(rules_json)
        semester = build_semester_from_overview(business_overview, overdue_rules, basic, remark_rules)
    except RulesError as exc:
        raise HTTPException(400, f"[비고 규칙] {exc}") from exc
    except ValueError as exc:
        raise HTTPException(400, f"[사업개요·기본 정보] {exc}") from exc

    overview_text = business_overview.strip()

    try:
        masked_refund, dropped_cols = run_stage(
            "환불자 목록 마스킹",
            lambda: mask_refund_workbook(refund_bytes),
        )
    except PipelineError as exc:
        raise HTTPException(400 if is_user_input_error(exc) else 500, str(exc)) from exc

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

    rental_evidence_image = "default"
    if rental_evidence is not None and rental_evidence.filename:
        from src.evidence_image import load_evidence_image
        try:
            rental_evidence_image = load_evidence_image(
                await _read_bytes(rental_evidence, "물품 대여 증빙 이미지"), "올린 물품 대여 증빙 이미지")
        except ValueError as exc:
            shutil.rmtree(work_dir, ignore_errors=True)
            raise HTTPException(400, f"[물품 대여 증빙 이미지] {exc}") from exc

    upload_config = UploadConfig(
        rental_evidence=rental_evidence_image,
        semester=semester,
        business_overview=overview_text,
        overdue_rules_text=rules_text,
        template_hwpx_path=template_hwpx,
        overdue_rules_summary=rules_summary,
        settings_json=settings_to_json(
            build_settings(
                semester_label=semester_label.strip(),
                author=semester.author,
                locker_deposit_remark=semester.locker_deposit_remark,
                film_fee_per_sheet=semester.film_fee_per_sheet,
                business_overview=overview_text,
                overdue_rules_text=rules_text,
                rules=semester.rules,
            )
        ),
    )

    try:
        prep = prepare_run(
            ledger_source=BytesIO(ledger_bytes),
            overdue_source=BytesIO(overdue_bytes),
            refund_source=masked_refund,
            upload=upload_config,
            work_dir=work_dir,
        )
    except PipelineError as exc:
        shutil.rmtree(work_dir, ignore_errors=True)
        if is_user_input_error(exc):
            raise HTTPException(400, str(exc)) from exc
        logger.exception("파이프라인 오류")
        raise HTTPException(500, str(exc)) from exc
    except Exception as exc:
        shutil.rmtree(work_dir, ignore_errors=True)
        logger.exception("처리 중 예기치 않은 오류")
        raise HTTPException(500, format_error_detail("문서 생성", exc)) from exc

    _previews[job_id] = PreviewJob(
        prep=prep,
        created_at=time.time(),
        masking_ok=masking_ok,
        masking_message=masking_message,
        rules_summary=rules_summary,
    )
    if _is_fetch(request):
        return JSONResponse({"preview_url": f"/preview/{job_id}"})
    return _render_preview(request, job_id)


def _get_preview(preview_id: str) -> PreviewJob:
    _cleanup_expired_jobs()
    job = _previews.get(preview_id)
    if not job:
        raise HTTPException(404, "미리보기가 만료되었거나 존재하지 않습니다. 처음부터 다시 업로드해 주세요.")
    return job


def _render_preview(request: Request, preview_id: str, message: str = "") -> HTMLResponse:
    job = _previews[preview_id]
    return templates.TemplateResponse(
        request,
        "preview.html",
        {
            "preview_id": preview_id,
            "view": build_preview_view(job.prep),
            "masking_ok": job.masking_ok,
            "masking_message": job.masking_message,
            "rules_summary": job.rules_summary,
            "expires_min": max(1, int((PREVIEW_TTL_SECONDS - (time.time() - job.created_at)) // 60)),
            "message": message,
        },
    )


@app.get("/preview/{preview_id}", response_class=HTMLResponse)
async def preview(request: Request, preview_id: str) -> HTMLResponse:
    _get_preview(preview_id)
    return _render_preview(request, preview_id)


@app.post("/build/{preview_id}", response_class=HTMLResponse)
async def build(request: Request, preview_id: str) -> HTMLResponse:
    job = _get_preview(preview_id)
    form = await request.form()
    job.prep.set_edits(parse_edit_form(form))

    try:
        result: PipelineResult = build_run(job.prep)
    except PipelineError as exc:
        if is_user_input_error(exc):
            raise HTTPException(400, str(exc)) from exc
        logger.exception("문서 생성 오류")
        raise HTTPException(500, str(exc)) from exc
    except Exception as exc:
        logger.exception("문서 생성 중 예기치 않은 오류")
        raise HTTPException(500, format_error_detail("문서 생성", exc)) from exc

    download_id = uuid.uuid4().hex
    _jobs[download_id] = DownloadJob(zip_path=result.zip_path, created_at=time.time())
    job.builds.append(download_id)

    s = result.summary
    from src.review_levels import LEVEL_HELP, LEVELS
    from src.pipeline import group_by_kind
    review_groups = []
    for lv in LEVELS:
        rows = [r for r in result.review_rows if r["단계"] == lv]
        review_groups.append({
            "level": lv, "help": LEVEL_HELP[lv], "count": len(rows),
            "kinds": [{"kind": k, "lines": [r["줄"].split("] ", 1)[-1] for r in g]} for k, g in group_by_kind(rows)],
        })
    return templates.TemplateResponse(
        request,
        "result.html",
        {
            "job_id": download_id,
            "preview_id": preview_id,
            "edit_count": sum(len(v) for v in job.prep.edits.values()),
            "masking_ok": job.masking_ok,
            "masking_message": job.masking_message,
            "business_count": s.business_count,
            "page_count": s.page_count,
            "hwpx_count": s.hwpx_count,
            "rules_summary": job.rules_summary,
            "review_groups": review_groups,
        },
    )


@app.get("/settings/example")
async def settings_example() -> JSONResponse:
    """기본값 설정 — 첫 화면 '기본값으로 되돌리기'에서 쓴다."""
    return JSONResponse(example_settings())


@app.post("/settings/from-file")
async def settings_from_file(file: UploadFile = File(...)) -> JSONResponse:
    """지난 학기 결과 zip(또는 그 안의 설정.json)에서 입력칸에 채울 값을 꺼낸다."""
    import zipfile as _zip
    from io import BytesIO as _BytesIO

    def _json_error(msg: str) -> JSONResponse:
        return JSONResponse({"detail": msg}, status_code=400)

    data = await file.read()
    if len(data) > MAX_FILE_BYTES:
        return _json_error("파일이 너무 큽니다.")

    text = None
    if data.startswith(b"PK"):
        try:
            with _zip.ZipFile(_BytesIO(data)) as zf:
                name = next((n for n in zf.namelist() if n.endswith("설정.json")), None)
                if name is None:
                    return _json_error("이 zip에는 지난 학기 입력 내용이 없습니다. 이 프로그램이 만든 결과 zip인지 확인해 주세요.")
                text = zf.read(name).decode("utf-8-sig")
        except _zip.BadZipFile:
            return _json_error("zip 파일을 열지 못했습니다.")
    else:
        text = data.decode("utf-8-sig", errors="replace")
    try:
        settings = json.loads(text)
    except json.JSONDecodeError:
        return _json_error("지난 학기 결과 zip(또는 그 안의 설정.json)을 올려 주세요.")
    if not isinstance(settings, dict) or "설정버전" not in settings:
        return _json_error("이 프로그램이 만든 결과 zip(또는 설정.json)이 아닙니다.")
    rules = settings.get("비고규칙")
    if isinstance(rules, dict):
        from src.rules_config import RETIRED_KEYS, _OLD_DEFAULTS, DEFAULT_RULES
        cleaned = {k: v for k, v in rules.items()
                   if k not in RETIRED_KEYS and _OLD_DEFAULTS.get(k) != v and not str(k).startswith("_")}
        settings["비고규칙"] = {**DEFAULT_RULES, **cleaned}
        settings["비고규칙_바뀜"] = any(DEFAULT_RULES.get(k) != v for k, v in cleaned.items())
    return JSONResponse(settings)


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

