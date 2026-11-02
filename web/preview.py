"""미리보기 화면용 데이터와 수정 폼 읽기 (4단계)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from web.runner import EDITABLE_FIELDS, PreparedRun

# 폼 이름에는 영문을 쓴다: e-{사업번호}-{페이지}-{remark|class|content}
FORM_KEYS = {"비고": "remark", "구분": "class", "내용": "content"}
FORM_FIELDS = {v: k for k, v in FORM_KEYS.items()}


@dataclass
class PageView:
    business_no: int
    index: int
    label: str
    date: str
    income_expense: str
    rows: list[tuple[str, str, str]]
    total: str
    values: dict[str, str]        # 지금 값(고친 값 포함), 키: remark/class/content
    originals: dict[str, str]
    edited: set[str]
    required: list[str] = field(default_factory=list)
    advisory: list[str] = field(default_factory=list)

    @property
    def needs_review(self) -> bool:
        return bool(self.required)

    def name(self, key: str) -> str:
        return f"e-{self.business_no}-{self.index}-{key}"


@dataclass
class BusinessView:
    business_no: int
    name: str
    pages: list[PageView]

    @property
    def review_count(self) -> int:
        return sum(len(p.required) for p in self.pages)


@dataclass
class PreviewView:
    businesses: list[BusinessView]
    input_required: list[str]
    input_advisory: list[str]
    page_count: int
    required_count: int
    advisory_count: int
    edit_count: int
    rows_per_page: int


def build_preview_view(prep: PreparedRun) -> PreviewView:
    by_page: dict[tuple[int, int], tuple[list[str], list[str]]] = {}
    for item in prep.result.review_items:
        req, adv = by_page.setdefault((item.business_no, item.page_index), ([], []))
        text = item.reason if item.advisory else (f"{item.target}: {item.reason}" if item.target else item.reason)
        (adv if item.advisory else req).append(text)

    businesses: list[BusinessView] = []
    for no, biz in sorted(prep.result.businesses.items()):
        pages: list[PageView] = []
        for idx, page in enumerate(biz.pages, start=1):
            req, adv = by_page.get((no, idx), ([], []))
            values = {FORM_KEYS[f]: prep.current(no, idx, f) for f in EDITABLE_FIELDS}
            originals = {FORM_KEYS[f]: prep.original(no, idx, f) for f in EDITABLE_FIELDS}
            edited = {FORM_KEYS[f] for f in prep.edits.get((no, idx), {})}
            pages.append(PageView(
                business_no=no, index=idx, label=page.business_no_label, date=page.date,
                income_expense=page.income_expense,
                rows=list(zip(page.items, page.quantities, page.unit_prices)),
                total=page.total_amount, values=values, originals=originals, edited=edited,
                required=req, advisory=adv,
            ))
        businesses.append(BusinessView(business_no=no, name=biz.business_name, pages=pages))

    return PreviewView(
        businesses=businesses,
        input_required=[i.format_line() for i in prep.input_issues if not i.advisory],
        input_advisory=[i.format_line() for i in prep.input_issues if i.advisory],
        page_count=prep.page_count,
        required_count=sum(1 for i in prep.result.review_items if not i.advisory),
        advisory_count=sum(1 for i in prep.result.review_items if i.advisory),
        edit_count=sum(len(v) for v in prep.edits.values()),
        rows_per_page=prep.layout.rows_per_page,
    )


def parse_edit_form(form: Mapping[str, Any]) -> dict[tuple[int, int, str], str]:
    """폼 값 → {(사업번호, 페이지, '비고'|'구분'|'내용'): 값}. 모르는 이름은 무시한다."""
    values: dict[tuple[int, int, str], str] = {}
    for key, value in form.items():
        parts = str(key).split("-")
        if len(parts) != 4 or parts[0] != "e" or parts[3] not in FORM_FIELDS:
            continue
        try:
            biz, idx = int(parts[1]), int(parts[2])
        except ValueError:
            continue
        values[(biz, idx, FORM_FIELDS[parts[3]])] = str(value)
    return values
