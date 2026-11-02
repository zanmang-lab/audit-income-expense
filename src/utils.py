"""공통 유틸리티."""
from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta
from typing import Any


def format_date(value: Any) -> str:
    """날짜를 `2026.03.23.` 형식으로 변환."""
    if value is None or isinstance(value, time):
        return ""
    d = parse_date_value(value)
    if d is None:
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
    return parse_date_value(value)


_DATE_PATTERNS = (
    # 2026-03-05, 2026.3.5., 2026/03/05, 2026. 3. 5 (뒤에 시간이 붙어도 허용)
    re.compile(r"^(\d{4})\s*[-./]\s*(\d{1,2})\s*[-./]\s*(\d{1,2})\s*\.?(?:\s.*)?$"),
    # 2026년 3월 5일
    re.compile(r"^(\d{4})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일"),
    # 20260305
    re.compile(r"^(\d{4})(\d{2})(\d{2})$"),
)
_EXCEL_EPOCH = date(1899, 12, 30)


def parse_date_value(value: Any) -> date | None:
    """엑셀 날짜·날짜 글자·엑셀 일련번호를 date로. 연도가 없는 등 애매하면 None."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, (int, float)):
        # 엑셀 일련번호(약 1954~2119년 범위만 인정)
        if 20000 <= value <= 80000 and float(value).is_integer():
            return _EXCEL_EPOCH + timedelta(days=int(value))
        if 19000101 <= value <= 21001231 and float(value).is_integer():
            value = str(int(value))
        else:
            return None
    text = str(value).strip()
    if not text:
        return None
    for pattern in _DATE_PATTERNS:
        m = pattern.match(text)
        if not m:
            continue
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    return None


_AMOUNT_STRIP_RE = re.compile(r"[,\s원₩\\]")


def parse_amount_value(value: Any) -> tuple[int | None, bool]:
    """금액 칸을 정수로. 반환: (금액, 읽기 성공 여부). 빈칸은 (None, True).

    '4,000', '4,000원', '₩4,000', '-4,000', '(4,000)' 모두 허용.
    """
    if value is None or isinstance(value, bool):
        return None, True
    if isinstance(value, (int, float)):
        return int(round(float(value))), True
    text = str(value).strip()
    if not text or text in {"-", "–"}:
        return None, True
    negative = text.startswith("(") and text.endswith(")")
    if negative:
        text = text[1:-1]
    text = _AMOUNT_STRIP_RE.sub("", text)
    try:
        amount = int(round(float(text)))
    except ValueError:
        return None, False
    return (-amount if negative else amount), True


def parse_int_value(value: Any) -> int | None:
    """사업번호처럼 정수여야 하는 칸. '3', 3.0, ' 3 ' 허용."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if value.is_integer() else None
    text = str(value).strip()
    if re.fullmatch(r"\d+(\.0+)?", text):
        return int(float(text))
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
