"""비고 생성 (SPEC 6번)."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable

from .config_gen import OverdueRulesConfig, lookup_per_day
from .parse_ledger import LedgerRow, Page
from .rules_config import DEFAULT_RULESET, RuleSet
from .semester_config import SemesterConfig
from .utils import (
    format_amount,
    has_batchim,
    josa_ro_euro,
    parse_rental_goods,
    parse_amount_value,
    parse_date_value,
    same_calendar_date,
)
from .sheet_reader import (
    OVERDUE_COLUMNS,
    REFUND_COLUMNS,
    InputIssue,
    is_blank_row,
    resolve_columns,
)

LOCKER_FIXED_REMARK = (
    "전자 사물함은 대여비 4,000원, 일반 사물함은 대여비 2,000원"
)  # 레거시 기본값; 실제 출력은 SemesterConfig.locker_deposit_remark 사용



@dataclass
class OverdueRecord:
    return_date: Any
    goods: str
    name: str
    fee: int
    note: str | None


@dataclass
class RefundRecord:
    locker_no: str | None
    name: str
    amount: int | None
    reason: str | None


@dataclass
class RemarkReview:
    target: str
    reason: str
    advisory: bool = False


def _compact_name(name: str) -> str:
    return "".join(str(name).split())


def similar_names(name: str, pool: list[str], limit: int = 3) -> list[str]:
    """띄어쓰기를 무시하고 비슷한 이름 후보(한 글자 차이 등)를 찾는다. 자동으로 고르지는 않는다."""
    import difflib

    target = _compact_name(name)
    by_compact: dict[str, str] = {}
    for n in pool:
        by_compact.setdefault(_compact_name(n), n)
    by_compact.pop(target, None)
    hits = difflib.get_close_matches(target, list(by_compact), n=limit, cutoff=0.6)
    return [by_compact[h] for h in hits]


def _with_suggestions(reason: str, name: str, pool: list[str]) -> str:
    hits = similar_names(name, pool)
    return f"{reason} — 비슷한 이름: {', '.join(hits)}" if hits else reason


@dataclass
class RemarkResult:
    text: str
    review_items: list[RemarkReview]


def _sheet_rows(ws) -> list[tuple]:
    return [tuple(r) for r in ws.iter_rows(values_only=True)]


def _append_notes(issues: list[InputIssue] | None, source: str, notes: list[str]) -> None:
    if issues is None:
        return
    for note in notes:
        issues.append(InputIssue(source, note, advisory=True))


def parse_overdue_sheet(ws, issues: list[InputIssue] | None = None) -> list[OverdueRecord]:
    """연체료 목록. 헤더 이름으로 열을 찾고, 읽지 못한 행은 issues에 기록."""
    source = "연체료목록"
    rows = _sheet_rows(ws)
    cols = resolve_columns(rows, OVERDUE_COLUMNS, source)
    _append_notes(issues, source, cols.notes)

    records: list[OverdueRecord] = []
    for row_no, row in enumerate(rows[cols.header_row:], start=cols.header_row + 1):
        if is_blank_row(row):
            continue
        name = cols.get(row, "name")
        if name is None or not str(name).strip():
            continue
        raw_fee = cols.get(row, "fee")
        fee, ok = parse_amount_value(raw_fee)
        if not ok:
            if issues is not None:
                issues.append(InputIssue(
                    source, f"'{str(name).strip()}'의 연체료를 숫자로 읽지 못해 제외했습니다 "
                            f"(값: '{raw_fee}').", row=row_no))
            continue
        note = cols.get(row, "note")
        goods = cols.get(row, "goods")
        records.append(
            OverdueRecord(
                return_date=parse_date_value(cols.get(row, "return_date")),
                goods=str(goods or "").strip(),
                name=str(name).strip(),
                fee=fee or 0,
                note=str(note).strip() if note is not None and str(note).strip() else None,
            )
        )
    return records


def parse_refund_sheet(ws, issues: list[InputIssue] | None = None) -> list[RefundRecord]:
    """환불자 목록. 헤더 이름으로 열을 찾는다(이름·사유 중심, 연락처·계좌는 읽지 않음)."""
    source = "환불자목록"
    rows = _sheet_rows(ws)
    if not any(not is_blank_row(r) for r in rows[1:]):
        return []
    cols = resolve_columns(rows, REFUND_COLUMNS, source)
    _append_notes(issues, source, cols.notes)

    records: list[RefundRecord] = []
    for row in rows[cols.header_row:]:
        if is_blank_row(row):
            continue
        name = cols.get(row, "name")
        if name is None or not str(name).strip():
            continue
        locker = cols.get(row, "locker")
        reason = cols.get(row, "reason")
        amount, _ = parse_amount_value(cols.get(row, "amount"))
        records.append(
            RefundRecord(
                locker_no=str(locker).strip() if locker is not None else None,
                name=str(name).strip(),
                amount=amount,
                reason=str(reason).strip() if reason is not None and str(reason).strip() else None,
            )
        )
    return records


def _disambiguate_records(
    candidates: list[Any],
    amount: int | None,
    ledger_date: Any,
    amount_getter: Callable[[Any], int | None],
    date_getter: Callable[[Any], Any],
) -> tuple[Any | None, str | None]:
    if not candidates:
        return None, "매칭 후보 없음"

    pool = candidates
    if amount is not None:
        by_amount = [c for c in pool if amount_getter(c) == amount]
        if not by_amount:
            return None, f"금액 {amount:,}원 일치 건 없음"
        pool = by_amount

    if len(pool) == 1:
        return pool[0], None

    if ledger_date is not None:
        by_date = [c for c in pool if same_calendar_date(date_getter(c), ledger_date)]
        if len(by_date) == 1:
            return by_date[0], None
        if len(by_date) > 1:
            return None, "동명이인·동금액·동일 날짜로 구분 불가"
        return None, "동명이인·동금액, 날짜로 구분 불가"

    return None, "동명이인 여러 건, 금액/날짜로 구분 불가"


def _find_overdue_direct(
    records: list[OverdueRecord],
    name: str,
    amount: int,
    ledger_date: Any,
) -> tuple[OverdueRecord | None, str | None]:
    candidates = [r for r in records if r.name == name]
    return _disambiguate_records(
        candidates,
        amount,
        ledger_date,
        amount_getter=lambda r: r.fee,
        date_getter=lambda r: r.return_date,
    )


def _daenap_josa(note: str, depositor_name: str) -> str | None:
    """비고가 '입금자(')으로 입금' / '입금자 충당' 형태면 조사를, 아니면 None."""
    name = re.escape(depositor_name)
    lead = r"(?:^|[\s,(/])"
    m = re.search(lead + name + r"\s*['’‘\"]?\s*(으로|로)\s*입금", note)
    if m:
        return m.group(1)
    if re.search(lead + name + r"\s*['’‘\"]?\s*충당", note):
        return josa_ro_euro(depositor_name)
    return None


def _find_daenap_record(
    records: list[OverdueRecord],
    depositor_name: str,
    amount: int,
    ledger_date: Any,
) -> tuple[OverdueRecord | None, str | None, str | None]:
    candidates: list[tuple[OverdueRecord, str | None]] = []
    if depositor_name:
        for rec in records:
            josa = _daenap_josa(rec.note or "", depositor_name)
            if josa is not None:
                candidates.append((rec, josa))

    if not candidates:
        return None, None, None

    unique_recs: list[OverdueRecord] = []
    josa_map: dict[int, str | None] = {}
    for rec, josa in candidates:
        if id(rec) not in josa_map:
            unique_recs.append(rec)
            josa_map[id(rec)] = josa

    picked, err = _disambiguate_records(
        unique_recs,
        amount,
        ledger_date,
        amount_getter=lambda r: r.fee,
        date_getter=lambda r: r.return_date,
    )
    if picked is None:
        return None, None, err
    return picked, josa_map[id(picked)], None


def _daily_rates(goods_text: str, rules: OverdueRulesConfig) -> list[int]:
    goods = parse_rental_goods(goods_text)
    return [lookup_per_day(raw, rules) for _, _, raw in goods]


def _max_daily_rate(goods_text: str, rules: OverdueRulesConfig) -> int:
    """빌린 물품 중 1일 연체료가 가장 높은 품목의 일단가."""
    rates = _daily_rates(goods_text, rules)
    return max(rates) if rates else 0


def _calc_overdue_days(
    fee: int,
    goods_text: str,
    rules: OverdueRulesConfig,
) -> tuple[int | None, str | None]:
    goods = parse_rental_goods(goods_text)
    if not goods:
        return None, "대여물품 파싱 실패 또는 1일 단가 미정"

    rates = [lookup_per_day(raw, rules) for _, _, raw in goods]
    max_daily = max(rates)
    if max_daily <= 0:
        return None, "1일 단가 미정"

    review_reasons: list[str] = []

    if any(qty > 1 for _, qty, _ in goods):
        review_reasons.append(
            "동일 품목 복수 수량 대여 — 수량(개)과 연체일수를 혼동하지 않도록 확인"
        )

    if len(set(rates)) > 1:
        review_reasons.append("서로 다른 일단가 물품이 함께 대여됨")

    if fee < max_daily:
        review_reasons.append(
            f"연체료 {fee:,}원이 최고 1일 단가 {max_daily:,}원보다 작음"
        )

    # 총 연체료 = 빌린 것 중 최고 1일 단가 × 연체일수
    if fee % max_daily != 0:
        review_reasons.append(
            f"연체료 {fee:,}원이 최고 1일 단가 {max_daily:,}원으로 "
            "나누어떨어지지 않음"
        )
        days = None
    else:
        days = fee // max_daily

    if review_reasons:
        return days, "; ".join(review_reasons)

    return days, None


def _format_goods_for_remark(goods_text: str) -> str:
    goods = parse_rental_goods(goods_text)
    if not goods:
        return goods_text
    return ", ".join(raw for _, _, raw in goods)


def _build_rental_remark_line(
    depositor_name: str,
    renter_name: str,
    goods_text: str,
    fee: int,
    note: str | None,
    rules: OverdueRulesConfig,
    is_daenap: bool,
    josa: str | None,
    film_fee_per_sheet: int = 1000,
    remark_rules: RuleSet = DEFAULT_RULESET,
) -> tuple[str, list[RemarkReview]]:
    reviews: list[RemarkReview] = []
    fee_text = format_amount(fee)
    goods_display = _format_goods_for_remark(goods_text)
    note_kind = remark_rules.rental_note_kind(note)
    review_target = renter_name
    R = remark_rules

    subject = R.subject(renter_name, depositor_name, josa) if is_daenap else R.subject(renter_name)
    common = {"대상": subject, "물품": goods_display, "금액": fee_text}

    if note_kind == "필름값":
        if film_fee_per_sheet <= 0 or fee % film_fee_per_sheet != 0:
            reviews.append(
                RemarkReview(
                    target=review_target,
                    reason=(
                        f"필름값 금액이 {film_fee_per_sheet:,}원 단위로 "
                        "나누어떨어지지 않음"
                    ),
                )
            )
            m_text = ""
        else:
            m_text = str(fee // film_fee_per_sheet)
        return R.render("필름값", 장수=m_text, **common), reviews

    if note_kind == "손망실비":
        return R.render("손망실비", **common), reviews

    if note_kind == "감면":
        days, day_review = _calc_overdue_days(fee, goods_text, rules)
        reasons = [r for r in [day_review, "감면 건 — 연체일수·감면 사유 확인 필요"] if r]
        if days is not None:
            line = R.render("감면", 일수=days, **common)
        else:
            line = R.render("감면_일수불명", **common)
        reviews.append(RemarkReview(target=review_target, reason="; ".join(reasons)))
        return line, reviews

    days, day_review = _calc_overdue_days(fee, goods_text, rules)
    if days is not None and not day_review:
        line = R.render("연체", 일수=days, **common)
    elif days is not None:
        line = R.render("연체_검수", 일수=days, **common)
        reviews.append(
            RemarkReview(target=review_target, reason=day_review or "연체일수 역산 예외")
        )
    else:
        line = R.render("연체_일수불명", **common)
        reviews.append(
            RemarkReview(
                target=review_target,
                reason=day_review or "연체료·규정 단가로 연체일수 역산이 불가",
            )
        )
    return line, reviews


def _build_rental_remark_for_row(
    row: LedgerRow,
    overdue_records: list[OverdueRecord],
    rules: OverdueRulesConfig,
    film_fee_per_sheet: int = 1000,
    remark_rules: RuleSet = DEFAULT_RULESET,
) -> RemarkResult:
    depositor_name = row.item_text.strip()
    amount = int(row.income)
    reviews: list[RemarkReview] = []

    direct, direct_err = _find_overdue_direct(
        overdue_records, depositor_name, amount, row.date
    )
    if not direct and direct_err == "매칭 후보 없음":
        # 띄어쓰기만 다른 이름('김 철수' ↔ '김철수')은 같은 사람으로 보고 알린다.
        loose = [
            r for r in overdue_records
            if r.name != depositor_name and _compact_name(r.name) == _compact_name(depositor_name)
        ]
        if loose:
            direct, direct_err = _disambiguate_records(
                loose, amount, row.date,
                amount_getter=lambda r: r.fee, date_getter=lambda r: r.return_date,
            )
            if direct:
                reviews.append(RemarkReview(
                    target=depositor_name,
                    reason=f"띄어쓰기만 다른 이름 — 장부 '{depositor_name}'와 연체료목록 '{direct.name}'을 같은 사람으로 보았습니다",
                    advisory=True,
                ))
    if direct:
        line, line_reviews = _build_rental_remark_line(
            depositor_name=depositor_name,
            renter_name=direct.name,
            goods_text=direct.goods,
            fee=direct.fee,
            note=direct.note,
            rules=rules,
            is_daenap=False,
            josa=None,
            film_fee_per_sheet=film_fee_per_sheet,
            remark_rules=remark_rules,
        )
        reviews.extend(line_reviews)
        return RemarkResult(text=line, review_items=reviews)

    daenap_rec, josa, daenap_err = _find_daenap_record(
        overdue_records, depositor_name, amount, row.date
    )
    if daenap_rec:
        line, line_reviews = _build_rental_remark_line(
            depositor_name=depositor_name,
            renter_name=daenap_rec.name,
            goods_text=daenap_rec.goods,
            fee=daenap_rec.fee,
            note=daenap_rec.note,
            rules=rules,
            is_daenap=True,
            josa=josa,
            film_fee_per_sheet=film_fee_per_sheet,
            remark_rules=remark_rules,
        )
        reviews.extend(line_reviews)
        return RemarkResult(text=line, review_items=reviews)

    err = direct_err or daenap_err or "연체료 목록 매칭 실패"
    if err == "매칭 후보 없음":
        err = _with_suggestions(
            f"연체료 목록에서 '{depositor_name}'을(를) 찾지 못함", depositor_name,
            [r.name for r in overdue_records],
        )
    reviews.append(RemarkReview(target=depositor_name, reason=err))
    return RemarkResult(text="", review_items=reviews)


def _find_refund_by_name(
    records: list[RefundRecord],
    name: str,
) -> tuple[RefundRecord | None, str | None]:
    candidates = [r for r in records if r.name == name]
    if not candidates:
        candidates = [r for r in records if _compact_name(r.name) == _compact_name(name)]
    if not candidates:
        return None, _with_suggestions(
            f"환불자 목록에서 '{name}' 매칭 실패", name, [r.name for r in records]
        )
    if len(candidates) > 1:
        return None, f"환불자 목록에 '{name}' 동명이인 {len(candidates)}건"
    refund = candidates[0]
    if not refund.reason:
        return None, f"환불자 목록 '{name}' 사유 없음"
    return refund, None


def _build_locker_deposit_remark(deposit_remark: str) -> RemarkResult:
    return RemarkResult(text=deposit_remark, review_items=[])


def _format_refund_names_line(names: list[str], name_suffix: str) -> str:
    """여러 명 + 공통 접미사. 예: 이지원님, … 구예모님은 당첨"""
    if len(names) == 1:
        return f"{names[0]}님은 {name_suffix}"
    head = ", ".join(f"{n}님" for n in names[:-1])
    return f"{head}, {names[-1]}님은 {name_suffix}"


def _refund_single_remark_line(name: str, phrase: str, remark_rules: RuleSet) -> str:
    josa = "으로" if has_batchim(phrase) else "로"
    return remark_rules.render("환불", 이름=name, 사유=phrase, 조사=josa)


def _append_grouped_refund_lines(
    lines: list[str],
    entries: list[tuple[str, str]],
    remark_rules: RuleSet = DEFAULT_RULESET,
) -> None:
    """같은 환불 사유는 이름을 묶어 가독성 있게 출력 (비고규칙 '환불사유_묶음')."""
    grouped: dict[str, list[str]] = {}
    reason_order: list[str] = []
    for name, phrase in entries:
        if phrase not in grouped:
            grouped[phrase] = []
            reason_order.append(phrase)
        grouped[phrase].append(name)

    for phrase in reason_order:
        names = grouped[phrase]
        split = remark_rules.refund_groups.get(phrase)
        if split and len(names) >= 2:
            name_suffix, reason_line = split
            lines.append(_format_refund_names_line(names, name_suffix))
            lines.append(reason_line)
        else:
            for name in names:
                lines.append(_refund_single_remark_line(name, phrase, remark_rules))


def _build_locker_refund_remark(
    rows: list[LedgerRow],
    refund_records: list[RefundRecord],
    deposit_remark: str,
    remark_rules: RuleSet = DEFAULT_RULESET,
) -> RemarkResult:
    lines = [deposit_remark]
    reviews: list[RemarkReview] = []
    entries: list[tuple[str, str]] = []

    for row in rows:
        name = row.item_text.strip()
        refund, err = _find_refund_by_name(refund_records, name)
        if not refund:
            reviews.append(
                RemarkReview(
                    target=name,
                    reason=err or f"환불자 목록에서 '{name}' 매칭 실패",
                )
            )
            continue
        if refund.name != name:
            reviews.append(RemarkReview(
                target=name,
                reason=f"띄어쓰기만 다른 이름 — 장부 '{name}'와 환불자목록 '{refund.name}'을 같은 사람으로 보았습니다",
                advisory=True,
            ))
        entries.append((name, remark_rules.refund_phrase(refund.reason)))

    _append_grouped_refund_lines(lines, entries, remark_rules)
    return RemarkResult(text="\n".join(lines), review_items=reviews)


def build_page_remark(
    business_no: int,
    page: Page,
    overdue_records: list[OverdueRecord],
    refund_records: list[RefundRecord],
    semester: SemesterConfig,
) -> RemarkResult:
    role = semester.role_for(business_no)
    rules = semester.overdue_rules
    remark_rules = semester.rules

    if role == "locker":
        if page.income_expense == "수입":
            return _build_locker_deposit_remark(semester.locker_deposit_remark)
        if remark_rules.is_refund_page(page.rows[0].overview):
            return _build_locker_refund_remark(
                page.rows,
                refund_records,
                semester.locker_deposit_remark,
                remark_rules,
            )

    if role == "rental" and page.income_expense == "수입":
        lines: list[str] = []
        all_reviews: list[RemarkReview] = []
        for row in page.rows:
            result = _build_rental_remark_for_row(
                row,
                overdue_records,
                rules,
                film_fee_per_sheet=semester.film_fee_per_sheet,
                remark_rules=remark_rules,
            )
            if result.text.strip():
                lines.append(result.text.strip())
            all_reviews.extend(result.review_items)
        return RemarkResult(text="\n".join(lines), review_items=all_reviews)

    return RemarkResult(text="", review_items=[])
