"""페이지 칸 채우기 및 preview/검수 산출 (SPEC 5·7번)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

from .parse_ledger import LedgerRow, Page, split_pages
from .remarks import (
    OverdueRecord,
    RefundRecord,
    RemarkResult,
    build_page_remark,
)
from .semester_config import SemesterConfig
from .utils import format_amount, format_date


@dataclass
class ReviewItem:
    business_no: int
    page_index: int
    target: str
    reason: str
    advisory: bool = False

    def format_line(self) -> str:
        if self.advisory:
            return (
                f"[검수권장] (사업{self.business_no}/{self.page_index}페이지) "
                f"{self.reason}"
            )
        return (
            f"[사업{self.business_no} / {self.page_index}페이지 / {self.target}] "
            f"{self.reason}"
        )


@dataclass
class FilledPage:
    business_no_label: str
    business_name: str
    date: str
    income_expense: str
    classification: str
    content: str
    items: list[str]
    quantities: list[str]
    unit_prices: list[str]
    total_amount: str
    evidence: str = ""
    remark: str = ""
    review_flags: list[str] = field(default_factory=list)


@dataclass
class BusinessOutput:
    business_no: int
    business_name: str
    pages: list[FilledPage] = field(default_factory=list)


@dataclass
class EngineResult:
    businesses: dict[int, BusinessOutput]
    review_items: list[ReviewItem]


def _format_business_no_label(rows: list[LedgerRow]) -> str:
    business_no = rows[0].business_no
    details = [r.detail_no for r in rows if r.detail_no is not None]
    if not details:
        return str(business_no)
    try:
        detail_nums = [int(d) for d in details]
    except (TypeError, ValueError):
        first = details[0]
        last = details[-1]
        if first == last:
            return f"{business_no}-{first}"
        return f"{business_no}-{first}~{last}"

    first = min(detail_nums)
    last = max(detail_nums)
    if first == last:
        return f"{business_no}-{first}"
    return f"{business_no}-{first}~{last}"


def _page_content(rows: list[LedgerRow]) -> str:
    for row in rows:
        if row.overview:
            return row.overview
    return ""


def _fill_page(
    business_no: int,
    business_name: str,
    page_index: int,
    page: Page,
    overdue_records: list[OverdueRecord],
    refund_records: list[RefundRecord],
    semester: SemesterConfig,
) -> tuple[FilledPage, list[ReviewItem]]:
    reviews: list[ReviewItem] = []
    rows = page.rows

    classification = page.classification
    if not classification:
        for row in rows:
            if row.classification_needs_review:
                reviews.append(
                    ReviewItem(
                        business_no=business_no,
                        page_index=page_index,
                        target=_page_target(rows),
                        reason="장부 구분(F열) 공백",
                    )
                )
                break

    items: list[str] = []
    quantities: list[str] = []
    unit_prices: list[str] = []
    total = 0

    for row in rows:
        items.append(row.item_text)
        quantities.append("1")
        amount = int(row.income) if page.income_expense == "수입" else int(row.expense)
        unit_prices.append(format_amount(amount))
        total += amount

        if row.has_error:
            reviews.append(
                ReviewItem(
                    business_no=business_no,
                    page_index=page_index,
                    target=row.item_text or "(세목없음)",
                    reason="장부 오류여부 칸 값 존재",
                )
            )

    remark_result: RemarkResult = build_page_remark(
        business_no, page, overdue_records, refund_records, semester
    )
    remark_text = remark_result.text

    for item in remark_result.review_items:
        reviews.append(
            ReviewItem(
                business_no=business_no,
                page_index=page_index,
                target=item.target or _page_target(rows),
                reason=item.reason,
            )
        )

    if (
        semester.role_for(business_no) == "locker"
        and page.income_expense == "지출"
        and "환불" in (_page_content(rows) or "")
    ):
        reviews.append(
            ReviewItem(
                business_no=business_no,
                page_index=page_index,
                target="",
                reason=semester.locker_refund_advisory,
                advisory=True,
            )
        )

    filled = FilledPage(
        business_no_label=_format_business_no_label(rows),
        business_name=business_name,
        date=format_date(page.date),
        income_expense=page.income_expense,
        classification=classification,
        content=_page_content(rows),
        items=items,
        quantities=quantities,
        unit_prices=unit_prices,
        total_amount=format_amount(total),
        evidence="",
        remark=remark_text,
    )
    return filled, reviews


def _page_target(rows: list[LedgerRow]) -> str:
    if len(rows) == 1:
        return rows[0].item_text or "(세목없음)"
    names = [r.item_text for r in rows if r.item_text]
    if names:
        return names[0] if len(names) == 1 else f"{names[0]} 외 {len(names)-1}건"
    return "(세목없음)"


def run_engine(
    ledger_by_business: dict[int, list],
    semester: SemesterConfig,
    overdue_records: list[OverdueRecord],
    refund_records: list[RefundRecord],
) -> EngineResult:
    businesses: dict[int, BusinessOutput] = {}
    all_reviews: list[ReviewItem] = []
    business_name_map = semester.name_map()

    for business_no in sorted(ledger_by_business):
        rows = ledger_by_business[business_no]
        pages = split_pages(rows)
        business_name = business_name_map.get(str(business_no), "")
        output = BusinessOutput(business_no=business_no, business_name=business_name)

        for page_index, page in enumerate(pages, start=1):
            filled, reviews = _fill_page(
                business_no,
                business_name,
                page_index,
                page,
                overdue_records,
                refund_records,
                semester,
            )
            output.pages.append(filled)
            all_reviews.extend(reviews)

        businesses[business_no] = output

    return EngineResult(businesses=businesses, review_items=all_reviews)


def result_to_preview_dict(result: EngineResult) -> dict[str, Any]:
    preview: dict[str, Any] = {}
    for business_no, biz in sorted(result.businesses.items()):
        preview[str(business_no)] = {
            "사업명": biz.business_name,
            "페이지": [
                {
                    "사업번호": page.business_no_label,
                    "사업명": page.business_name,
                    "날짜": page.date,
                    "수입/지출": page.income_expense,
                    "구분": page.classification,
                    "내용": page.content,
                    "세목": page.items,
                    "수량": page.quantities,
                    "단가": page.unit_prices,
                    "총액": page.total_amount,
                    "증빙서류": page.evidence,
                    "비고": page.remark,
                }
                for page in biz.pages
            ],
        }
    return preview


def format_review_report(review_items: list[ReviewItem]) -> str:
    lines = [item.format_line() for item in review_items]
    return "\n".join(lines) + ("\n" if lines else "")


def filled_page_to_hwpx_dict(page: FilledPage) -> dict[str, Any]:
    """fill_hwpx.fill_document이 받는 페이지 dict로 변환."""
    rows = list(zip(page.items, page.quantities, page.unit_prices))
    if len(rows) > 13:
        raise ValueError(
            f"페이지 '{page.business_no_label}' 세목 {len(rows)}줄 — "
            f"13줄 분할이 필요합니다."
        )
    return {
        "사업번호": page.business_no_label,
        "사업명": page.business_name,
        "날짜": page.date,
        "수입지출": page.income_expense,
        "구분": page.classification,
        "내용": page.content,
        "총액": page.total_amount,
        "비고": page.remark,
        "rows": rows,
    }


def export_hwpx_files(
    result: EngineResult,
    template_path: str,
    output_dir: str,
    filename_prefix: str | None = None,
) -> list[str]:
    """사업명별 .hwpx 파일을 output_dir에 생성."""
    from .fill_hwpx import fill_document

    prefix = filename_prefix or "수입지출내역서"
    written: list[str] = []
    for business_no, biz in sorted(result.businesses.items()):
        if not biz.pages:
            continue
        pages = [filled_page_to_hwpx_dict(p) for p in biz.pages]
        out_name = f"{prefix}_{business_no}_{biz.business_name}.hwpx"
        out_path = os.path.join(output_dir, out_name)
        fill_document(template_path, pages, out_path)
        written.append(out_path)
    return written
