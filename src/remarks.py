"""비고 생성 (SPEC 6번)."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable

from .config_gen import OverdueRulesConfig, lookup_per_day
from .parse_ledger import LedgerRow, Page
from .semester_config import SemesterConfig
from .utils import (
    format_amount,
    has_batchim,
    josa_ro_euro,
    normalize_refund_reason,
    parse_amount,
    parse_rental_goods,
    same_calendar_date,
)

LOCKER_FIXED_REMARK = (
    "전자 사물함은 대여비 4,000원, 일반 사물함은 대여비 2,000원"
)  # 레거시 기본값; 실제 출력은 SemesterConfig.locker_deposit_remark 사용

DAENAP_PATTERNS = [
    re.compile(r"(?P<name>.+?)'으로 입금"),
    re.compile(r"(?P<name>.+?)'로 입금"),
    re.compile(r"(?P<name>.+?)\s*충당"),
]


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


@dataclass
class RemarkResult:
    text: str
    review_items: list[RemarkReview]


def parse_overdue_sheet(ws) -> list[OverdueRecord]:
    records: list[OverdueRecord] = []
    for row in ws.iter_rows(min_row=2, values_only=True):
        name = row[2]
        if not name:
            continue
        fee = parse_amount(row[3])
        if fee is None:
            fee = 0
        records.append(
            OverdueRecord(
                return_date=row[0],
                goods=str(row[1] or ""),
                name=str(name).strip(),
                fee=fee,
                note=str(row[4]).strip() if row[4] else None,
            )
        )
    return records


def parse_refund_sheet(ws) -> list[RefundRecord]:
    records: list[RefundRecord] = []
    for row in ws.iter_rows(min_row=2, values_only=True):
        name = row[1]
        if not name:
            continue
        records.append(
            RefundRecord(
                locker_no=str(row[0]) if row[0] is not None else None,
                name=str(name).strip(),
                amount=parse_amount(row[3]),
                reason=str(row[4]).strip() if row[4] else None,
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


def _find_daenap_record(
    records: list[OverdueRecord],
    depositor_name: str,
    amount: int,
    ledger_date: Any,
) -> tuple[OverdueRecord | None, str | None, str | None]:
    candidates: list[tuple[OverdueRecord, str | None]] = []
    for rec in records:
        note = rec.note or ""
        for pattern in DAENAP_PATTERNS:
            match = pattern.search(note)
            if not match:
                continue
            if match.group("name").strip() != depositor_name:
                continue
            josa = (
                "으로"
                if "'으로" in note
                else "로"
                if "'로" in note
                else josa_ro_euro(depositor_name)
            )
            candidates.append((rec, josa))
            break

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


def _classify_rental_note(note: str | None) -> str:
    """연체료목록 비고 열 분류 (SPEC 6-3). 대납 문구는 '연체'로 처리(일수 계산 대상)."""
    text = (note or "").strip()
    if text == "필름값":
        return "필름값"
    if text == "손망실비":
        return "손망실비"
    if text == "감면":
        return "감면"
    return "연체"


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
) -> tuple[str, list[RemarkReview]]:
    reviews: list[RemarkReview] = []
    fee_text = format_amount(fee)
    goods_display = _format_goods_for_remark(goods_text)
    note_kind = _classify_rental_note(note)
    review_target = renter_name

    if is_daenap:
        used_josa = josa or josa_ro_euro(depositor_name)
        subject = f"{renter_name}('{depositor_name}'{used_josa} 입금)님 "
    else:
        subject = f"{renter_name}님 "

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
        line = f"{subject}폴라로이드 필름 {m_text}장 추가로 소모하여 {fee_text}원 부과"
        return line, reviews

    if note_kind == "손망실비":
        line = f"{subject}{goods_display} 손망실비 {fee_text}원 부과"
        return line, reviews

    if note_kind == "감면":
        days, day_review = _calc_overdue_days(fee, goods_text, rules)
        reasons = [r for r in [day_review, "감면 건 — 연체일수·감면 사유 확인 필요"] if r]
        combined_review = "; ".join(reasons)
        if days is not None:
            line = (
                f"{subject}{goods_display} 대여 후 {days}일 연체되어 "
                f"연체료 {fee_text}원 감면 (검수필요)"
            )
        else:
            line = (
                f"{subject}{goods_display} 대여 후 연체되어 "
                f"연체료 {fee_text}원 감면 (검수필요)"
            )
        reviews.append(RemarkReview(target=review_target, reason=combined_review))
        return line, reviews

    days, day_review = _calc_overdue_days(fee, goods_text, rules)
    if days is not None and not day_review:
        line = (
            f"{subject}{goods_display} 대여 후 {days}일 연체되어 "
            f"연체료 {fee_text}원 부과"
        )
    elif days is not None:
        line = (
            f"{subject}{goods_display} 대여 후 {days}일 연체되어 "
            f"연체료 {fee_text}원 부과 (검수필요)"
        )
        reviews.append(
            RemarkReview(
                target=review_target,
                reason=day_review or "연체일수 역산 예외",
            )
        )
    else:
        line = (
            f"{subject}{goods_display} 대여 후 연체되어 "
            f"연체료 {fee_text}원 부과 (연체일수 검수필요)"
        )
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
) -> RemarkResult:
    depositor_name = row.item_text.strip()
    amount = int(row.income)
    reviews: list[str] = []

    direct, direct_err = _find_overdue_direct(
        overdue_records, depositor_name, amount, row.date
    )
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
        )
        reviews.extend(line_reviews)
        return RemarkResult(text=line, review_items=reviews)

    err = direct_err or daenap_err or "연체료 목록 매칭 실패"
    reviews.append(RemarkReview(target=depositor_name, reason=err))
    return RemarkResult(text="", review_items=reviews)


def _find_refund_by_name(
    records: list[RefundRecord],
    name: str,
) -> tuple[RefundRecord | None, str | None]:
    candidates = [r for r in records if r.name == name]
    if not candidates:
        return None, f"환불자 목록에서 '{name}' 매칭 실패"
    if len(candidates) > 1:
        return None, f"환불자 목록에 '{name}' 동명이인 {len(candidates)}건"
    refund = candidates[0]
    if not refund.reason:
        return None, f"환불자 목록 '{name}' 사유 없음"
    return refund, None


def _build_locker_deposit_remark(deposit_remark: str) -> RemarkResult:
    return RemarkResult(text=deposit_remark, review_items=[])


def _refund_reason_phrase(reason: str) -> str:
    """비고 문장에 쓸 사유 표현."""
    return {
        "중복 입금": "두 번 송금",
    }.get(reason, reason)


def _format_refund_names_line(names: list[str], name_suffix: str) -> str:
    """여러 명 + 공통 접미사. 예: 이지원님, … 구예모님은 당첨"""
    if len(names) == 1:
        return f"{names[0]}님은 {name_suffix}"
    head = ", ".join(f"{n}님" for n in names[:-1])
    return f"{head}, {names[-1]}님은 {name_suffix}"


def _refund_single_remark_line(name: str, reason: str) -> str:
    phrase = _refund_reason_phrase(reason)
    josa = "으로" if has_batchim(phrase) else "로"
    return f"{name}님은 {phrase}{josa} 인하여 환불 진행"


def _append_grouped_refund_lines(
    lines: list[str],
    entries: list[tuple[str, str]],
) -> None:
    """같은 환불 사유는 이름을 묶어 가독성 있게 출력."""
    grouped: dict[str, list[str]] = {}
    reason_order: list[str] = []
    for name, reason in entries:
        if reason not in grouped:
            grouped[reason] = []
            reason_order.append(reason)
        grouped[reason].append(name)

    group_splits: dict[str, tuple[str, str]] = {
        "당첨 등록 포기": ("당첨", "등록 포기로 인하여 환불 진행"),
        "중복 등록 포기": ("중복", "등록 포기로 인하여 환불 진행"),
    }

    for reason in reason_order:
        names = grouped[reason]
        split = group_splits.get(reason)
        if split and len(names) >= 2:
            name_suffix, reason_line = split
            lines.append(_format_refund_names_line(names, name_suffix))
            lines.append(reason_line)
        else:
            for name in names:
                lines.append(_refund_single_remark_line(name, reason))


def _build_locker_refund_remark(
    rows: list[LedgerRow],
    refund_records: list[RefundRecord],
    deposit_remark: str,
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
        entries.append((name, normalize_refund_reason(refund.reason)))

    _append_grouped_refund_lines(lines, entries)
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

    if role == "locker":
        if page.income_expense == "수입":
            return _build_locker_deposit_remark(semester.locker_deposit_remark)
        if "환불" in (page.rows[0].overview or ""):
            return _build_locker_refund_remark(
                page.rows,
                refund_records,
                semester.locker_deposit_remark,
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
            )
            if result.text.strip():
                lines.append(result.text.strip())
            all_reviews.extend(result.review_items)
        return RemarkResult(text="\n".join(lines), review_items=all_reviews)

    return RemarkResult(text="", review_items=[])
