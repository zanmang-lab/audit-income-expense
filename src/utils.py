"""공통 유틸리티."""
from __future__ import annotations

import re
from datetime import date, datetime, time
from typing import Any


def format_date(value: Any) -> str:
    """날짜를 `2026.03.23.` 형식으로 변환."""
    if value is None:
        return ""
    if isinstance(value, datetime):
        d = value.date()
    elif isinstance(value, date):
        d = value
    elif isinstance(value, time):
        return ""
    else:
        return ""
    return f"{d.year}.{d.month:02d}.{d.day:02d}."


def format_amount(value: Any) -> str:
    """금액을 콤마 포함 문자열로 변환."""
    if value is None or value == "":
        return ""
    if isinstance(value, str):
        cleaned = value.replace(",", "").replace("원", "").strip()
        if not cleaned or cleaned == "#VALUE!":
            return ""
        try:
            value = float(cleaned)
        except ValueError:
            return ""
    try:
        amount = int(round(float(value)))
    except (TypeError, ValueError):
        return ""
    return f"{amount:,}"


def parse_amount(value: Any) -> int | None:
    text = format_amount(value)
    if not text:
        return None
    return int(text.replace(",", ""))


def has_batchim(text: str) -> bool:
    if not text:
        return False
    ch = text[-1]
    if "가" <= ch <= "힣":
        return (ord(ch) - ord("가")) % 28 != 0
    return False


def josa_ro_euro(name: str) -> str:
    return "으로" if has_batchim(name) else "로"


GOODS_QTY_RE = re.compile(r"(\d+)\s*개")


def parse_rental_goods(goods_text: str) -> list[tuple[str, int, str]]:
    """대여물품 문자열을 (기본명, 수량, 원문조각) 목록으로 분리."""
    if not goods_text:
        return []
    parts = [p.strip() for p in str(goods_text).split(",")]
    result: list[tuple[str, int, str]] = []
    for part in parts:
        if not part:
            continue
        qty_match = GOODS_QTY_RE.search(part)
        qty = int(qty_match.group(1)) if qty_match else 1
        base = GOODS_QTY_RE.sub("", part).strip()
        base = re.sub(r"\([^)]*\)", "", base).strip()
        if base:
            result.append((base, qty, part.strip()))
    return result


def distinct_goods_base_names(goods_texts: list[str]) -> list[str]:
    names: set[str] = set()
    for text in goods_texts:
        for base, _, _ in parse_rental_goods(text):
            names.add(base)
    return sorted(names)


def same_calendar_date(a: Any, b: Any) -> bool:
    """두 값이 같은 날짜(연·월·일)인지 비교."""
    da = _to_date(a)
    db = _to_date(b)
    if da is None or db is None:
        return False
    return da == db


def _to_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return None


def normalize_refund_reason(reason: str | None) -> str:
    if not reason:
        return ""
    mapping = {
        "당첨등록포기": "당첨 등록 포기",
        "두번송금": "중복 입금",
        "중복등록포기": "중복 등록 포기",
        "규칙미준수": "규칙 미준수",
    }
    return mapping.get(reason.strip(), reason.strip())
