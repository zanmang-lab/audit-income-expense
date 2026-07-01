"""연체료 규정 이미지 → OverdueRulesConfig 변환."""
from __future__ import annotations

import base64
import json
import os
import re
from io import BytesIO
from pathlib import Path

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
_OCR_DIGIT_FIX = str.maketrans({"O": "0", "o": "0", "l": "1", "I": "1", "S": "5", "Z": "2"})


def _strip_markup(line: str) -> str:
    return re.sub(r"<[^>]+>", "", line).strip()


def _normalize_ocr_text(text: str) -> str:
    return text.replace("日", "일").replace("／", "/")


def _normalize_amount_digits(raw: str) -> int | None:
    cleaned = raw.translate(_OCR_DIGIT_FIX)
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


def parse_overdue_rules_text(text: str) -> OverdueRulesConfig:
    """규정표 텍스트(OCR·SVG)에서 물품별 1일 단가를 추출한다."""
    normalized = _normalize_ocr_text(text)
    lines = [_strip_markup(raw.strip()) for raw in normalized.splitlines() if raw.strip()]

    for parser in (_parse_inline_rules, _parse_table_fee_rules):
        result = parser(lines)
        if result is not None:
            return result

    raise ValueError(
        "연체료 규정 표에서 1일 단가를 찾지 못했습니다. "
        "'1DAY ₩1,000' 형식 표 또는 '물품명 — 1,000원/일' 형식이 보이는지 확인해 주세요."
    )


def extract_text_from_svg(data: bytes) -> str:
    text = data.decode("utf-8", errors="ignore")
    parts = re.findall(r">([^<]+)<", text)
    return "\n".join(p.strip() for p in parts if p.strip())


def _ocr_lines_from_output(output) -> list[str]:
    if output is None:
        return []
    if hasattr(output, "txts"):
        return [str(t).strip() for t in (output.txts or ()) if str(t).strip()]
    if isinstance(output, tuple):
        result = output[0]
        if not result:
            return []
        return [str(item[1]).strip() for item in result if len(item) > 1 and item[1]]
    return []


_rapid_ocr = None


def _get_rapid_ocr():
    """한국어 인식 모델(PP-OCRv4 korean). 최초 실행 시 모델 다운로드(~25MB)."""
    global _rapid_ocr
    if _rapid_ocr is not None:
        return _rapid_ocr

    errors: list[str] = []
    try:
        from rapidocr import EngineType, LangRec, RapidOCR
        from rapidocr.utils.typings import ModelType, OCRVersion

        _rapid_ocr = RapidOCR(
            params={
                "Rec.lang_type": LangRec.KOREAN,
                "Rec.engine_type": EngineType.ONNXRUNTIME,
                "Rec.ocr_version": OCRVersion.PPOCRV4,
                "Rec.model_type": ModelType.MOBILE,
            }
        )
        return _rapid_ocr
    except Exception as exc:
        errors.append(f"rapidocr: {exc}")

    try:
        from rapidocr_onnxruntime import RapidOCR as LegacyRapidOCR

        _rapid_ocr = LegacyRapidOCR()
        return _rapid_ocr
    except Exception as exc:
        errors.append(f"rapidocr_onnxruntime: {exc}")

    raise ImportError(" / ".join(errors))


def extract_text_with_rapidocr(data: bytes) -> str:
    """사진·스캔 이미지 OCR (한국어 모델, 32비트 Windows 지원)."""
    import numpy as np
    from PIL import Image

    image = Image.open(BytesIO(data))
    if image.mode != "RGB":
        image = image.convert("RGB")
    engine = _get_rapid_ocr()
    try:
        output = engine(data)
    except Exception:
        output = engine(np.array(image))
    lines = _ocr_lines_from_output(output)
    return _normalize_ocr_text("\n".join(lines))


def extract_text_with_easyocr(data: bytes) -> str:
    import easyocr
    import numpy as np
    from PIL import Image

    image = Image.open(BytesIO(data)).convert("RGB")
    reader = easyocr.Reader(["ko", "en"], gpu=False, verbose=False)
    lines = reader.readtext(np.array(image), detail=0, paragraph=True)
    return "\n".join(str(line).strip() for line in lines if str(line).strip())


def parse_with_openai_vision(data: bytes, ext: str) -> OverdueRulesConfig:
    from openai import OpenAI

    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise ValueError("OPENAI_API_KEY가 설정되어 있지 않습니다.")

    mime = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".gif": "image/gif",
        ".bmp": "image/bmp",
    }.get(ext.lower(), "image/png")

    client = OpenAI(api_key=api_key)
    prompt = """이 이미지는 학생회 물품 대여 연체료 규정표입니다.
물품별 1일 연체료를 JSON으로만 출력하세요. 다른 설명은 금지.

형식:
{
  "default_per_day": 숫자,
  "categories": [
    {"per_day": 숫자, "keywords": ["물품키워드", ...]}
  ]
}

- '그 외', '기본' 행은 default_per_day에 넣습니다.
- keywords는 장부·연체료 목록에 나올 수 있는 짧은 물품명 조각입니다.
- 금액은 원 단위 정수입니다."""

    response = client.chat.completions.create(
        model=os.environ.get("OPENAI_VISION_MODEL", "gpt-4o-mini"),
        response_format={"type": "json_object"},
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": (
                                f"data:{mime};base64,"
                                f"{base64.b64encode(data).decode('ascii')}"
                            )
                        },
                    },
                ],
            }
        ],
        max_tokens=1500,
    )
    raw = response.choices[0].message.content or "{}"
    return parse_overdue_rules_data(json.loads(raw))


def parse_overdue_rules_image(data: bytes, filename: str) -> OverdueRulesConfig:
    """업로드 이미지에서 연체료 계산 규칙을 추출한다."""
    if len(data) < 8:
        raise ValueError("연체료 규정 이미지가 비어 있습니다.")

    ext = Path(filename).suffix.lower()
    errors: list[str] = []

    if os.environ.get("OPENAI_API_KEY", "").strip():
        try:
            return parse_with_openai_vision(data, ext)
        except Exception as exc:
            errors.append(f"AI 인식 실패: {exc}")

    if ext == ".svg" or data.lstrip()[:1] == b"<":
        text = extract_text_from_svg(data)
        if text.strip():
            try:
                return parse_overdue_rules_text(text)
            except ValueError as exc:
                errors.append(str(exc))
        else:
            errors.append("SVG에서 규정 문구를 찾지 못했습니다.")

    ocr_attempts: list[tuple[str, callable]] = [
        ("rapidocr", extract_text_with_rapidocr),
        ("easyocr", extract_text_with_easyocr),
    ]
    for name, extractor in ocr_attempts:
        try:
            text = extractor(data)
            if not text.strip():
                errors.append(f"{name} 결과가 비어 있습니다.")
                continue
            try:
                return parse_overdue_rules_text(text)
            except ValueError as exc:
                errors.append(f"{name} 텍스트 파싱 실패: {exc}")
        except ImportError as exc:
            errors.append(f"{name} 미설치: {exc}")
        except Exception as exc:
            errors.append(f"{name} 실패: {exc}")

    detail = " / ".join(errors) if errors else "알 수 없는 오류"
    raise ValueError(
        "연체료 규정 이미지를 읽지 못했습니다. "
        "표가 선명한 사진·스캔인지 확인해 주세요. "
        f"({detail})"
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
