"""연체료 규정 텍스트(웹 입력칸) → OverdueRulesConfig 변환."""
from __future__ import annotations

import re

from .config_gen import CategoryRule, OverdueRulesConfig, parse_overdue_rules_data

# 서버가 최신 파서를 로드했는지 /api/health 로 확인
PARSER_BUILD_ID = "table-format-v2"

_AMOUNT_RE = re.compile(r"(\d[\d,]*)\s*원")
_DAILY_FEE_LINE_RE = re.compile(
    r"(?:1\s*DAY|IDAY)\s*[W₩w]?\s*(\d[\d,OolISZ,]*)",
    re.IGNORECASE,
)
_DEFAULT_HINT_RE = re.compile(r"그\s*외|기본")
_SEPARATORS = ("—", "–", "-", ":", "：", "|")
_FOOTER_HINT_RE = re.compile(r"메일|인스타|위원회|@|naver|instagram", re.IGNORECASE)
_DIGIT_FIX = str.maketrans({"O": "0", "o": "0", "l": "1", "I": "1", "S": "5", "Z": "2"})


def _strip_markup(line: str) -> str:
    return re.sub(r"<[^>]+>", "", line).strip()


def _normalize_text(text: str) -> str:
    return text.replace("日", "일").replace("／", "/")


def _normalize_amount_digits(raw: str) -> int | None:
    cleaned = raw.translate(_DIGIT_FIX)
    digits = re.sub(r"[^\d]", "", cleaned)
    if not digits:
        return None
    value = int(digits)
    return value if value > 0 else None


def _parse_fee_amount(line: str) -> int | None:
    """'1DAY W1,000' / '1DAY ₩3000' / '1,000원/일' 등에서 1일 단가 추출."""
    compact = line.replace("₩", "W").replace(",", "").replace(" ", "")
    match = _DAILY_FEE_LINE_RE.search(compact) or _DAILY_FEE_LINE_RE.search(line)
    if match:
        return _normalize_amount_digits(match.group(1))

    won_match = _AMOUNT_RE.search(line)
    if won_match:
        return _normalize_amount_digits(won_match.group(1))

    if re.search(r"(?:1\s*DAY|IDAY|[W₩])", line, re.IGNORECASE):
        digits = re.findall(r"\d[\d,OolISZ,]*", line)
        for raw in digits:
            amount = _normalize_amount_digits(raw)
            if amount and amount >= 100:
                return amount
    return None


def _looks_like_fee_line(line: str) -> bool:
    compact = line.replace(" ", "")
    return bool(
        re.search(r"(?:1DAY|IDAY)", compact, re.IGNORECASE)
        or re.search(r"[W₩]\s*\d", line)
        or ("/일" in line and _AMOUNT_RE.search(line))
    )


def _split_keywords(text: str) -> list[str]:
    normalized = text.replace("'", ",").replace("'", ",")
    keywords: list[str] = []
    for part in re.split(r"[,，、]", normalized):
        kw = re.sub(r"\s+", "", part.strip())
        if not kw or kw.isdigit():
            continue
        if not re.search(r"[가-힣A-Za-z]", kw):
            continue
        if kw not in keywords:
            keywords.append(kw)
    return keywords


def _is_default_keyword(keyword: str) -> bool:
    compact = keyword.replace(" ", "")
    return bool(_DEFAULT_HINT_RE.search(keyword) or compact.startswith("그외"))


def _parse_inline_rules(lines: list[str]) -> OverdueRulesConfig | None:
    """한 줄에 '물품명 — N원/일' 형식."""
    default_per_day = 2000
    by_day: dict[int, list[str]] = {}

    for line in lines:
        if not line or line.startswith("※") or ("예시" in line and "업로드" in line):
            continue
        if "연체료 규정" in line and "물품별" in line:
            continue

        amount_match = _AMOUNT_RE.search(line)
        if not amount_match:
            continue

        amount = _normalize_amount_digits(amount_match.group(1))
        if not amount:
            continue

        if _DEFAULT_HINT_RE.search(line):
            default_per_day = amount
            continue

        left = line[: amount_match.start()]
        left = re.sub(r"/\s*일.*$", "", left)
        for sep in _SEPARATORS:
            if sep in left:
                left = left.split(sep, 1)[0]
                break

        keywords = _split_keywords(left)
        if not keywords:
            continue

        bucket = by_day.setdefault(amount, [])
        for kw in keywords:
            if kw not in bucket:
                bucket.append(kw)

    if not by_day:
        return None

    return OverdueRulesConfig(
        default_per_day=default_per_day,
        categories=[
            CategoryRule(per_day=per_day, keywords=keywords)
            for per_day, keywords in sorted(by_day.items(), key=lambda x: -x[0])
        ],
    )


def _parse_table_fee_rules(lines: list[str]) -> OverdueRulesConfig | None:
    """
    표 형식: 금액 행(1DAY ₩1,000) 아래 물품명 행.
    OCR이 열 순서와 다르게 읽어도 키워드로 열을 추정한다.
    """
    in_table = False
    fee_amounts: list[int] = []
    item_lines: list[str] = []

    for line in lines:
        compact = line.replace(" ", "")
        if "연체료" in compact and "규정" in compact:
            in_table = True
            continue
        if not in_table:
            continue
        if _FOOTER_HINT_RE.search(line):
            break

        if _looks_like_fee_line(line):
            amount = _parse_fee_amount(line)
            if amount:
                fee_amounts.append(amount)
            continue

        if re.search(r"[가-힣]", line):
            item_lines.append(line)

    if not fee_amounts:
        return None

    all_keywords: list[str] = []
    for item_line in item_lines:
        all_keywords.extend(_split_keywords(item_line))

    if not all_keywords and len(fee_amounts) == 1:
        return OverdueRulesConfig(
            default_per_day=fee_amounts[0],
            categories=[],
        )

    if not all_keywords:
        return None

    # 일반적 3열 표: [저가, 고가, 기본] 순으로 OCR되는 경우가 많음
    default_per_day = fee_amounts[-1]
    by_day: dict[int, list[str]] = {}

    low_fee = fee_amounts[0]
    mid_fee = fee_amounts[1] if len(fee_amounts) > 1 else fee_amounts[0]
    if len(fee_amounts) >= 3:
        default_per_day = fee_amounts[2]
        mid_fee = fee_amounts[1]

    for kw in all_keywords:
        if _is_default_keyword(kw):
            continue
        if "보조배터리" in kw:
            by_day.setdefault(low_fee, []).append("보조배터리")
        else:
            by_day.setdefault(mid_fee, []).append(kw)

    if not by_day:
        return None

    categories = [
        CategoryRule(per_day=per_day, keywords=keywords)
        for per_day, keywords in sorted(by_day.items(), key=lambda x: -x[0])
    ]
    return OverdueRulesConfig(default_per_day=default_per_day, categories=categories)


def _parse_day_fee_colon_rules(lines: list[str]) -> OverdueRulesConfig | None:
    """
    '1DAY ₩1,000: 보조배터리' / '1DAY ₩2,000: 그 외 물품' 형식.
    금액과 물품명이 한 줄에 콜론으로 구분된 입력.
    """
    default_per_day = 2000
    by_day: dict[int, list[str]] = {}
    parsed_any = False

    for line in lines:
        if not line or line.startswith("※"):
            continue
        if "연체료 규정" in line and "물품별" in line:
            continue

        amount = _parse_fee_amount(line)
        if not amount:
            continue

        parsed_any = True
        rest = line
        for sep in (":", "："):
            if sep in line:
                rest = line.split(sep, 1)[1].strip()
                break

        if _is_default_keyword(rest):
            default_per_day = amount
            continue

        keywords = _split_keywords(rest)
        if not keywords:
            continue

        bucket = by_day.setdefault(amount, [])
        for kw in keywords:
            if kw not in bucket:
                bucket.append(kw)

    if not parsed_any:
        return None
    if not by_day:
        return OverdueRulesConfig(default_per_day=default_per_day, categories=[])

    return OverdueRulesConfig(
        default_per_day=default_per_day,
        categories=[
            CategoryRule(per_day=per_day, keywords=keywords)
            for per_day, keywords in sorted(by_day.items(), key=lambda x: -x[0])
        ],
    )


def parse_overdue_rules_text(text: str) -> OverdueRulesConfig:
    """규정표 텍스트(웹 입력칸)에서 물품별 1일 단가를 추출한다."""
    normalized = _normalize_text(text)
    lines = [_strip_markup(raw.strip()) for raw in normalized.splitlines() if raw.strip()]

    for parser in (_parse_day_fee_colon_rules, _parse_inline_rules, _parse_table_fee_rules):
        result = parser(lines)
        if result is not None:
            return result

    raise ValueError(
        "연체료 규정 표에서 1일 단가를 찾지 못했습니다. "
        "'1DAY ₩1,000: 물품명' 또는 '물품명 — 1,000원/일' 형식인지 확인해 주세요."
    )


def rules_to_summary(rules: OverdueRulesConfig) -> str:
    """UI·zip용 요약."""
    lines = [f"기본 {rules.default_per_day:,}원/일"]
    for cat in rules.categories:
        kw = ", ".join(cat.keywords[:5])
        if len(cat.keywords) > 5:
            kw += f" 외 {len(cat.keywords) - 5}개"
        lines.append(f"{cat.per_day:,}원/일 — {kw}")
    return "\n".join(lines)
