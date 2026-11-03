"""엑셀 시트 공통 읽기 — 헤더 이름으로 열 찾기 + 입력 확인 항목.

열 위치(A, B, C…)가 아니라 헤더 글자로 열을 찾는다.
- 헤더를 하나도 알아보지 못하면 기존 열 순서(fallback)로 읽고 '입력확인 권장'을 남긴다.
- 필수 열 일부만 못 찾으면 추정하지 않고 오류를 낸다(어느 열을 찾았는지 함께 안내).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Sequence

HEADER_SCAN_ROWS = 10


@dataclass(frozen=True)
class ColumnSpec:
    key: str
    label: str  # 사용자에게 보여줄 이름
    synonyms: tuple[str, ...]
    fallback_index: int
    required: bool = True


@dataclass
class InputIssue:
    """입력 파일에서 읽지 못했거나 제외한 항목."""

    source: str  # 예: "장부"
    message: str
    row: int | None = None  # 엑셀 행 번호(1부터)
    advisory: bool = False
    auto: bool = False  # 프로그램이 바꿔 읽은 것(글자 날짜 → 날짜 등)

    def format_line(self) -> str:
        where = f"{self.source} {self.row}행" if self.row else self.source
        tag = "[자동처리]" if self.auto else ("[입력확인-권장]" if self.advisory else "[입력확인]")
        return f"{tag} {where}: {self.message}"


@dataclass
class ColumnMap:
    indices: dict[str, int | None]
    header_row: int  # 헤더가 있는 엑셀 행 번호(1부터). 데이터는 그다음 행부터.
    used_fallback: bool = False
    notes: list[str] = field(default_factory=list)

    def get(self, row: Sequence[Any], key: str) -> Any:
        idx = self.indices.get(key)
        if idx is None or idx >= len(row):
            return None
        return row[idx]


class ColumnError(ValueError):
    """필수 열을 찾지 못함."""


def normalize_header(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip().lower()
    return re.sub(r"\s+", "", text)


def _match_row(headers: list[str], specs: Sequence[ColumnSpec]) -> dict[str, int]:
    """헤더 한 줄에서 열 위치를 찾는다. 정확히 일치를 먼저, 그다음 포함 관계."""
    found: dict[str, int] = {}
    used: set[int] = set()

    for exact in (True, False):
        for spec in specs:
            if spec.key in found:
                continue
            for idx, header in enumerate(headers):
                if idx in used or not header:
                    continue
                for syn in spec.synonyms:
                    syn_n = normalize_header(syn)
                    if (header == syn_n) if exact else (syn_n in header):
                        found[spec.key] = idx
                        used.add(idx)
                        break
                if spec.key in found:
                    break
    return found


def resolve_columns(
    rows: Sequence[Sequence[Any]],
    specs: Sequence[ColumnSpec],
    source: str,
) -> ColumnMap:
    """시트 앞부분(rows)에서 헤더 줄을 찾아 열 위치를 정한다."""
    best_found: dict[str, int] = {}
    best_row = 1
    for row_no, row in enumerate(rows[:HEADER_SCAN_ROWS], start=1):
        headers = [normalize_header(v) for v in row]
        found = _match_row(headers, specs)
        if len(found) > len(best_found):
            best_found, best_row = found, row_no

    required = [s for s in specs if s.required]
    found_required = [s for s in required if s.key in best_found]

    # 헤더를 거의 못 알아봄 → 예전 방식(열 순서)으로 읽는다.
    if len(found_required) <= 1 and len(best_found) <= 1:
        order = ", ".join(f"{_col_letter(s.fallback_index)} {s.label}" for s in specs)
        return ColumnMap(
            indices={s.key: s.fallback_index for s in specs},
            header_row=1,
            used_fallback=True,
            notes=[f"헤더를 알아보지 못해 기존 열 순서({order})로 읽었습니다."],
        )

    missing = [s for s in required if s.key not in best_found]
    if missing:
        header_text = ", ".join(
            str(v).strip() for v in rows[best_row - 1] if v is not None and str(v).strip()
        )
        need = ", ".join(
            f"'{s.label}'(예: {'/'.join(s.synonyms[:3])})" for s in missing
        )
        raise ColumnError(
            f"{source}에서 {need} 열을 찾지 못했습니다. "
            f"{best_row}행에서 읽은 헤더: [{header_text}]"
        )

    notes = []
    for spec in specs:
        if not spec.required and spec.key not in best_found:
            notes.append(f"'{spec.label}' 열이 없어 비워 둡니다.")
    return ColumnMap(
        indices={s.key: best_found.get(s.key) for s in specs},
        header_row=best_row,
        notes=notes,
    )


def _col_letter(index: int) -> str:
    letters = ""
    n = index + 1
    while n:
        n, rem = divmod(n - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


def is_blank_row(row: Sequence[Any]) -> bool:
    return all(v is None or (isinstance(v, str) and not v.strip()) for v in row)


# ── 시트별 열 정의 ────────────────────────────────────────────

LEDGER_COLUMNS: tuple[ColumnSpec, ...] = (
    ColumnSpec("date", "날짜", ("날짜", "일자", "거래일", "거래일자"), 0),
    ColumnSpec("business_no", "사업번호", ("사업번호", "사업no"), 1),
    ColumnSpec("detail_no", "세부번호", ("세부번호", "세부no"), 2, required=False),
    ColumnSpec("item", "기재내용", ("기재내용", "세목", "기재사항", "내역"), 3),
    ColumnSpec("classification", "구분", ("구분",), 5, required=False),
    ColumnSpec("overview", "개요", ("개요", "적요", "내용"), 6, required=False),
    ColumnSpec("income", "수입", ("수입", "수입금액", "입금액"), 7),
    ColumnSpec("expense", "지출", ("지출", "지출금액", "출금액"), 8),
    ColumnSpec("error", "오류여부", ("오류여부", "오류"), 9, required=False),
)

OVERDUE_COLUMNS: tuple[ColumnSpec, ...] = (
    ColumnSpec("return_date", "반납일", ("반납일", "입금일", "반납일(입금일)", "날짜"), 0, required=False),
    ColumnSpec("goods", "대여물품", ("대여물품", "물품", "대여품목", "품목"), 1),
    ColumnSpec("name", "성명", ("성명", "이름", "대여자", "대여자명"), 2),
    ColumnSpec("fee", "연체료", ("연체료", "금액", "납부액", "입금액"), 3),
    ColumnSpec("note", "비고", ("비고", "메모", "참고"), 4, required=False),
)

# 순서 중요: '사유'를 '금액'보다 먼저 찾아야 '환불사유'가 금액으로 잡히지 않는다.
REFUND_COLUMNS: tuple[ColumnSpec, ...] = (
    ColumnSpec("locker", "사물함 번호", ("사물함번호", "사물함"), 0, required=False),
    ColumnSpec("name", "이름", ("이름", "성명", "신청자", "환불자"), 1),
    ColumnSpec("reason", "사유", ("사유", "환불사유"), 4),
    ColumnSpec("amount", "환불금액", ("환불금액", "금액", "환불액"), 3, required=False),
)
