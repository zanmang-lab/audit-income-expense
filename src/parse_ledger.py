"""장부 파싱 및 페이지 분할 (SPEC 3번)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, BinaryIO, Iterator

import openpyxl
from openpyxl.workbook.workbook import Workbook
from openpyxl.worksheet.worksheet import Worksheet

from .semester_config import SemesterConfig
from .sheet_reader import (
    LEDGER_COLUMNS,
    ColumnError,
    ColumnMap,
    InputIssue,
    is_blank_row,
    resolve_columns,
)
from .utils import parse_amount_value, parse_date_value, parse_int_value

MAX_ROWS_PER_PAGE = 13


@dataclass
class LedgerRow:
    date: Any
    business_no: int
    detail_no: Any
    item_text: str
    overview: str | None
    income: float
    expense: float
    has_error: bool
    classification: str = ""
    classification_needs_review: bool = False


@dataclass
class Page:
    date: Any
    income_expense: str
    classification: str
    rows: list[LedgerRow] = field(default_factory=list)

    @property
    def key(self) -> tuple[Any, str, str]:
        return (self.date, self.income_expense, self.classification)


def _cell_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


_TOTAL_WORDS = {"합계", "소계", "총계", "누계", "계", "총합계"}


def _looks_like_total_row(row: tuple) -> bool:
    for v in row:
        if isinstance(v, str) and v.replace(" ", "") in _TOTAL_WORDS:
            return True
    return False


def _show(value: Any) -> str:
    text = "" if value is None else str(value).strip()
    return f"'{text}'" if text else "빈칸"


def _read_rows(ws: Worksheet) -> list[tuple]:
    return [tuple(r) for r in ws.iter_rows(values_only=True)]


def _iter_from_rows(
    rows: list[tuple],
    cols: ColumnMap,
    issues: list[InputIssue] | None,
) -> Iterator[LedgerRow]:
    def report(row_no: int, message: str, advisory: bool = False) -> None:
        if issues is not None:
            issues.append(InputIssue("장부", message, row=row_no, advisory=advisory))

    for row_no, row in enumerate(rows[cols.header_row:], start=cols.header_row + 1):
        if is_blank_row(row):
            continue

        raw_biz = cols.get(row, "business_no")
        raw_date = cols.get(row, "date")
        raw_income = cols.get(row, "income")
        raw_expense = cols.get(row, "expense")
        income_v, income_ok = parse_amount_value(raw_income)
        expense_v, expense_ok = parse_amount_value(raw_expense)
        has_amount = bool(income_v) or bool(expense_v) or not income_ok or not expense_ok

        business_no = parse_int_value(raw_biz)
        if business_no is None:
            if has_amount and not _looks_like_total_row(row):
                report(row_no, f"사업번호를 읽지 못해 제외했습니다 (값: {_show(raw_biz)}).")
            continue

        row_date = parse_date_value(raw_date)
        if row_date is None:
            if has_amount:
                report(row_no, f"날짜를 읽지 못해 제외했습니다 (값: {_show(raw_date)}). "
                               "2026-03-05 같은 형식으로 적어 주세요.")
            continue

        if not income_ok or not expense_ok:
            bad = raw_income if not income_ok else raw_expense
            report(row_no, f"수입/지출 금액을 숫자로 읽지 못해 제외했습니다 (값: {_show(bad)}).")
            continue

        income = income_v or 0
        expense = expense_v or 0
        if income < 0 or expense < 0:
            report(row_no, f"음수 금액(수입 {income:,} / 지출 {expense:,})이라 제외했습니다. "
                           "취소·환급을 어떻게 적을지 확인해 주세요.")
            continue
        if income > 0 and expense > 0:
            report(row_no, f"한 행에 수입({income:,})과 지출({expense:,})이 함께 있어 제외했습니다. "
                           "행을 나눠 주세요.")
            continue
        if income == 0 and expense == 0:
            report(row_no, "수입·지출 금액이 모두 0(또는 빈칸)이라 제외했습니다.", advisory=True)
            continue

        raw_overview = cols.get(row, "overview")
        raw_class = cols.get(row, "classification")
        classification = str(raw_class).strip() if raw_class is not None else ""
        raw_error = cols.get(row, "error")

        yield LedgerRow(
            date=row_date,
            business_no=business_no,
            detail_no=cols.get(row, "detail_no"),
            item_text=_cell_str(cols.get(row, "item")),
            overview=_cell_str(raw_overview) if raw_overview is not None else None,
            income=float(income),
            expense=float(expense),
            has_error=raw_error is not None and str(raw_error).strip() != "",
            classification=classification,
            classification_needs_review=not classification,
        )


def iter_ledger_rows(ws: Worksheet, issues: list[InputIssue] | None = None) -> Iterator[LedgerRow]:
    """워크시트에서 유효한 장부 행을 순서대로 반환. 제외한 행은 issues에 기록."""
    rows = _read_rows(ws)
    cols = resolve_columns(rows, LEDGER_COLUMNS, "장부")
    yield from _iter_from_rows(rows, cols, issues)


def _scan_sheet(ws: Worksheet) -> tuple[int, ColumnMap | None, list[tuple], Exception | None]:
    rows = _read_rows(ws)
    try:
        cols = resolve_columns(rows, LEDGER_COLUMNS, "장부")
    except ColumnError as exc:
        return 0, None, rows, exc
    return sum(1 for _ in _iter_from_rows(rows, cols, None)), cols, rows, None


def _choose_sheet(wb: Workbook, preferred: str | None):
    scans = {}
    if preferred and preferred in wb.sheetnames:
        scan = _scan_sheet(wb[preferred])
        if scan[0] > 0:
            return preferred, scan
        scans[preferred] = scan

    best_name, best_scan = "", None
    for name in wb.sheetnames:
        scan = scans.get(name) or _scan_sheet(wb[name])
        scans[name] = scan
        if scan[0] > 0 and (best_scan is None or scan[0] > best_scan[0]):
            best_name, best_scan = name, scan
    if best_scan is not None:
        return best_name, best_scan

    column_errors = [s[3] for s in scans.values() if s[3] is not None]
    if column_errors:
        raise column_errors[0]
    raise ColumnError(
        "장부 데이터를 찾지 못했습니다. 날짜·사업번호·기재내용·수입·지출 열이 있는 "
        "시트가 포함된 장부 파일인지 확인해 주세요. (다른 목록 파일을 장부 칸에 올리지 않았는지도 확인해 주세요.)"
    )


def find_ledger_sheet(wb: Workbook) -> str:
    """업로드 장부에서 데이터가 있는 시트를 자동 선택."""
    return _choose_sheet(wb, None)[0]


def resolve_ledger_sheet(wb: Workbook, preferred: str | None = None) -> str:
    """선호 시트명이 있으면 먼저 시도하고, 없거나 비어 있으면 자동 탐색."""
    return _choose_sheet(wb, preferred)[0]


def parse_ledger_workbook(
    wb: Workbook,
    sheet_name: str | None = None,
    semester: SemesterConfig | None = None,
    issues: list[InputIssue] | None = None,
) -> dict[int, list[LedgerRow]]:
    """열린 워크북에서 사업번호별 장부 행 목록을 반환. 제외한 행은 issues에 기록."""
    preferred = sheet_name
    if semester is not None and preferred is None:
        preferred = semester.ledger_sheet or None
    name, (_, cols, rows, _) = _choose_sheet(wb, preferred)

    if issues is not None:
        if len(wb.sheetnames) > 1:
            issues.append(InputIssue("장부", f"여러 시트 중 '{name}' 시트를 읽었습니다.", advisory=True))
        for note in cols.notes:
            issues.append(InputIssue("장부", note, advisory=True))

    grouped: dict[int, list[LedgerRow]] = {}
    for ledger_row in _iter_from_rows(rows, cols, issues):
        grouped.setdefault(ledger_row.business_no, []).append(ledger_row)
    return grouped


def split_pages(rows: list[LedgerRow], max_rows: int = MAX_ROWS_PER_PAGE) -> list[Page]:
    """사업번호 내 행을 페이지 키 기준으로 분할. 키가 같아도 max_rows줄(양식 세목 줄 수)이면 새 페이지."""
    pages: list[Page] = []
    current: Page | None = None

    for row in rows:
        income_expense = "수입" if row.income > 0 else "지출"
        key = (row.date, income_expense, row.classification)

        if (
            current is None
            or current.key != key
            or len(current.rows) >= max_rows
        ):
            current = Page(
                date=row.date,
                income_expense=income_expense,
                classification=row.classification,
            )
            pages.append(current)
        current.rows.append(row)

    return pages


def load_ledger_workbook(source: str | BinaryIO, read_only: bool = True) -> Workbook:
    """파일 경로 또는 바이너리 스트림에서 장부 워크북을 연다."""
    return openpyxl.load_workbook(source, read_only=read_only, data_only=True)
