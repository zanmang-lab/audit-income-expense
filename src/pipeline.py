"""페이지 칸 채우기 및 preview/검수 산출 (SPEC 5·7번)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Sequence

from .parse_ledger import MAX_ROWS_PER_PAGE, LedgerRow, Page, split_pages
from .template_layout import TemplateLayout
from .remarks import (
    OverdueRecord,
    RefundRecord,
    RemarkResult,
    build_page_remark,
)
from .semester_config import SemesterConfig
from .sheet_reader import InputIssue
from .utils import format_amount, format_date


@dataclass
class ReviewItem:
    business_no: int
    page_index: int
    target: str
    reason: str
    advisory: bool = False
    auto: bool = False  # 프로그램이 계산·추론·변환해서 채운 것 — 사용자가 확인해야 함

    @property
    def level(self) -> str:
        from .review_levels import level_of
        return level_of(self)

    def where(self, labels: dict[tuple[int, int], str] | None = None) -> str:
        label = (labels or {}).get((self.business_no, self.page_index))
        page = f"사업{self.business_no} {self.page_index}쪽" + (f"({label})" if label else "")
        target = self.target if self.target and self.target not in ("비고", "페이지") else ""
        return f"{page} · {target}" if target else page

    def format_line(self, labels: dict[tuple[int, int], str] | None = None) -> str:
        return f"[{self.level}] {self.where(labels)}: {self.reason}"


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
    evidence_image: bool = False  # 증빙서류 칸에 물품 대여 안내 그림을 넣는 쪽
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


def _detail_gap(rows: list[LedgerRow]) -> str:
    """세부번호가 숫자이고 중간이 비면 '2, 5'처럼 돌려준다."""
    try:
        nums = [int(r.detail_no) for r in rows if r.detail_no is not None]
    except (TypeError, ValueError):
        return ""
    if len(nums) < 2:
        return ""
    if sorted(nums) == list(range(min(nums), max(nums) + 1)) and len(set(nums)) == len(nums):
        return ""
    return ", ".join(str(n) for n in nums)


@dataclass
class FeeSplit:
    done: bool
    message: str = ""
    items: list[str] = field(default_factory=list)
    quantities: list[str] = field(default_factory=list)
    unit_prices: list[str] = field(default_factory=list)


FEE_ITEM_NAME = "수수료"


def split_refund_fee(rows: list[LedgerRow], refund_records: list[RefundRecord], max_rows: int) -> FeeSplit:
    """환불 페이지의 '원금+수수료' 금액을 원금 줄과 '수수료 n × 금액' 줄로 나눈다.

    검산이 될 때만 나눈다: 모든 행이 환불자 목록과 연결되고, (장부 금액 − 목록 환불금액)이
    모든 행에서 같은 양수여야 한다. 하나라도 아니면 나누지 않고 이유를 알린다.
    """
    from .remarks import _find_refund_by_name

    principals: list[int] = []
    diffs: list[int] = []
    for row in rows:
        rec, _ = _find_refund_by_name(refund_records, row.item_text.strip())
        if rec is None or rec.amount is None:
            return FeeSplit(False)  # 매칭 실패는 비고 쪽 검수필요로 이미 알림
        principals.append(int(rec.amount))
        diffs.append(int(row.expense) - int(rec.amount))
    if not rows or all(d == 0 for d in diffs):
        return FeeSplit(False)
    if len(set(diffs)) != 1 or diffs[0] <= 0 or any(p <= 0 for p in principals):
        detail = ", ".join(f"{r.item_text.strip()} {int(r.expense):,}−{p:,}" for r, p in zip(rows, principals))
        return FeeSplit(False, f"장부 금액과 환불자 목록 금액의 차이가 일정하지 않아 수수료를 나누지 않았습니다({detail}). 직접 확인해 주세요.")
    if len(rows) + 1 > max_rows:
        return FeeSplit(False, "세목 칸이 모자라 수수료 줄을 따로 넣지 못했습니다. 장부 금액(원금+수수료) 그대로 두었습니다.")
    fee, n = diffs[0], len(rows)
    return FeeSplit(
        True,
        f"장부 금액 {int(rows[0].expense):,}원 등을 환불자 목록 금액(원금)과 '{FEE_ITEM_NAME} {n} × {fee:,}원'으로 나눴습니다. "
        f"총액은 그대로입니다. 실제 이체 수수료와 맞는지 확인해 주세요.",
        items=[r.item_text for r in rows] + [FEE_ITEM_NAME],
        quantities=["1"] * n + [str(n)],
        unit_prices=[format_amount(p) for p in principals] + [format_amount(fee)],
    )


# 환급 구분과 수입/지출이 맞아야 하는 짝 (26-1 장부: 환불 = 지출·수입의 환급, 입금 = 수입·지출의 환급)
_REFUND_CLASS_SIDE = {"수입의환급": "지출", "지출의환급": "수입"}


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
    max_rows: int = MAX_ROWS_PER_PAGE,
) -> tuple[FilledPage, list[ReviewItem]]:
    reviews: list[ReviewItem] = []
    rows = page.rows

    def note(reason: str, *, auto: bool = False, advisory: bool = False, target: str = "") -> None:
        reviews.append(ReviewItem(
            business_no=business_no, page_index=page_index, target=target or _page_target(rows),
            reason=reason, advisory=advisory or auto, auto=auto,
        ))

    if page.split_reason:
        what = "개요(내용)" if page.split_reason == "개요" else "기재내용(거래처)"
        note(f"앞 쪽과 날짜·구분은 같지만 {what}가 달라 새 쪽으로 나눴습니다. 한 쪽으로 합쳐야 하면 직접 합쳐 주세요.", auto=True)

    gap = _detail_gap(rows)
    if gap:
        note(f"세부번호가 이어지지 않습니다({gap}) — 사업번호 표기 '{_format_business_no_label(rows)}'가 맞는지 확인해 주세요.")

    if not _page_content(rows):
        note("장부 개요가 비어 있어 내용 칸을 비웠습니다.")

    classification = page.classification
    expected_side = _REFUND_CLASS_SIDE.get(classification)
    if expected_side and expected_side != page.income_expense:
        note(f"구분이 '{classification}'이면 {expected_side}이어야 하는데 장부 금액이 {page.income_expense} 칸에 있습니다. "
             f"환불(돌려준 돈)은 지출·'수입의환급', 돌려받은 돈은 수입·'지출의환급'입니다. 장부의 구분이나 금액 칸을 확인해 주세요.")
    if not classification:
        for row in rows:
            if row.classification_needs_review:
                reviews.append(
                    ReviewItem(
                        business_no=business_no,
                        page_index=page_index,
                        target=_page_target(rows),
                        reason="장부 구분 칸 공백",
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
                advisory=item.advisory or item.auto,
                auto=item.auto,
            )
        )

    if (
        semester.role_for(business_no) == "locker"
        and page.income_expense == "지출"
        and semester.rules.is_refund_page(_page_content(rows))
    ):
        split = split_refund_fee(rows, refund_records, max_rows)
        if split.done:
            items, quantities, unit_prices = split.items, split.quantities, split.unit_prices
            note(split.message, auto=True)
        elif split.message:
            note(split.message, advisory=True)
        elif semester.rules.refund_fee_advisory:
            note(semester.rules.refund_fee_advisory, advisory=True)

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
        evidence_image=semester.role_for(business_no) == "rental" and page.income_expense == "수입",
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
    max_rows_per_page: int = MAX_ROWS_PER_PAGE,
) -> EngineResult:
    businesses: dict[int, BusinessOutput] = {}
    all_reviews: list[ReviewItem] = []
    business_name_map = semester.name_map()

    for business_no in sorted(ledger_by_business):
        rows = ledger_by_business[business_no]
        role = semester.role_for(business_no)
        pages = split_pages(rows, max_rows_per_page, split_on_item=role not in ("locker", "rental"))
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
                max_rows_per_page,
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


REPORT_NOTE = (
    "※ 세목은 장부 기재내용, 수량은 1, 단가는 장부 금액으로 넣은 초안입니다. "
    "영수증 품목·수량으로 적어야 하는 쪽과 증빙서류 칸은 직접 작성해 주세요.\n"
    "※ 같은 내용이 '검수목록.xlsx'에 체크리스트로도 있습니다. 쪽은 사업별 파일 기준입니다(괄호 안은 문서의 사업번호 표기)."
)


def page_labels(result: "EngineResult") -> dict[tuple[int, int], str]:
    """(사업번호, 쪽) → 문서의 사업번호 표기(예: 3-8~10). 검수목록에서 쪽을 찾기 쉽게."""
    return {(no, i): p.business_no_label
            for no, biz in result.businesses.items() for i, p in enumerate(biz.pages, start=1)}


def review_rows(
    review_items: Sequence[ReviewItem],
    input_issues: Sequence[InputIssue] = (),
    labels: dict[tuple[int, int], str] | None = None,
) -> list[dict[str, Any]]:
    """검수 항목을 3단계 순서(꼭 고칠 것 → 확인할 것 → 참고)로. 입력 파일 항목이 먼저."""
    from .review_levels import LEVELS, kind_of

    rows: list[dict[str, Any]] = []
    for i in input_issues:
        rows.append({"단계": i.level, "종류": kind_of(i.message, i.level), "사업": None, "쪽": None, "문서표기": "",
                     "대상": i.where, "내용": i.message, "줄": i.format_line()})
    for r in review_items:
        rows.append({"단계": r.level, "종류": kind_of(r.reason, r.level), "사업": r.business_no, "쪽": r.page_index,
                     "문서표기": (labels or {}).get((r.business_no, r.page_index), ""),
                     "대상": r.target, "내용": r.reason, "줄": r.format_line(labels)})
    order = {lv: n for n, lv in enumerate(LEVELS)}
    return sorted(rows, key=lambda x: order[x["단계"]])  # 같은 단계 안에서는 원래 순서 유지


def group_by_kind(rows: Sequence[dict[str, Any]]) -> list[tuple[str, list[dict[str, Any]]]]:
    """같은 종류끼리 묶고, 많은 종류부터(같으면 처음 나온 순서)."""
    groups: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        groups.setdefault(r["종류"], []).append(r)
    return sorted(groups.items(), key=lambda kv: -len(kv[1]))


def format_review_report(
    review_items: list[ReviewItem],
    input_issues: Sequence[InputIssue] = (),
    labels: dict[tuple[int, int], str] | None = None,
) -> str:
    """검수목록.txt — 꼭 고칠 것 / 확인할 것 / 참고 순서로 묶는다."""
    from .review_levels import LEVEL_HELP, LEVELS

    rows = review_rows(review_items, input_issues, labels)
    lines = [REPORT_NOTE, ""]
    for lv in LEVELS:
        items = [r for r in rows if r["단계"] == lv]
        if not items:
            continue
        lines += [f"■ {lv} ({len(items)}건) — {LEVEL_HELP[lv]}"]
        for kind, group in group_by_kind(items):
            lines.append(f"  ▸ {kind} ({len(group)}건)")
            lines += [f"    {r['줄']}" for r in group]
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def filled_page_to_hwpx_dict(page: FilledPage) -> dict[str, Any]:
    """fill_hwpx.fill_document이 받는 페이지 dict로 변환."""
    rows = list(zip(page.items, page.quantities, page.unit_prices))
    return {
        "사업번호": page.business_no_label,
        "사업명": page.business_name,
        "날짜": page.date,
        "수입지출": page.income_expense,
        "구분": page.classification,
        "내용": page.content,
        "총액": page.total_amount,
        "비고": page.remark,
        "증빙이미지": page.evidence_image,
        "rows": rows,
    }


def export_hwpx_files(
    result: EngineResult,
    template_path: str,
    output_dir: str,
    filename_prefix: str | None = None,
    author: dict[str, str] | None = None,
    layout: TemplateLayout | None = None,
    problems: list[str] | None = None,
    evidence_image=None,
) -> list[str]:
    """사업명별 .hwpx 파일을 output_dir에 생성. author는 모든 페이지의 작성자 칸에 들어간다.

    evidence_image가 있으면 물품 대여 사업 수입 쪽 증빙서류 칸에 그 그림을 넣는다."""
    from .fill_hwpx import fill_document

    prefix = filename_prefix or "수입지출내역서"
    written: list[str] = []
    for business_no, biz in sorted(result.businesses.items()):
        if not biz.pages:
            continue
        pages = [{**(author or {}), **filled_page_to_hwpx_dict(p)} for p in biz.pages]
        out_name = f"{prefix}_{business_no}_{biz.business_name}.hwpx"
        out_path = os.path.join(output_dir, out_name)
        fill_document(template_path, pages, out_path, layout=layout, problems=problems,
                      evidence_image=evidence_image)
        written.append(out_path)
    return written


def export_combined_hwpx(
    result: EngineResult,
    template_path: str,
    out_path: str,
    author: dict[str, str] | None = None,
    layout: TemplateLayout | None = None,
    problems: list[str] | None = None,
    evidence_image=None,
) -> str | None:
    """모든 사업의 쪽을 사업번호 순서대로 한 파일(통합본)에. 쪽이 없으면 None."""
    from .fill_hwpx import fill_document

    pages = [{**(author or {}), **filled_page_to_hwpx_dict(p)}
             for _, biz in sorted(result.businesses.items()) for p in biz.pages]
    if not pages:
        return None
    fill_document(template_path, pages, out_path, layout=layout, problems=problems,
                  evidence_image=evidence_image)
    return out_path
