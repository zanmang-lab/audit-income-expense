"""양식(.hwpx) 칸 위치 찾기 — 3단계.

원칙: 지금 쓰는 학복위 양식에서는 결과가 절대 달라지지 않는다.
칸마다 아래 순서로 정한다.
  1) 기존 좌표의 칸이 있고, 그 왼쪽 라벨도 맞으면 → 기존 좌표 (가장 안전)
  2) 아니면 양식에서 라벨(예: '사업번호')을 찾아 그 오른쪽 칸 → 다른 양식 지원
  3) 라벨을 못 찾았지만 기존 좌표 칸은 있으면 → 기존 좌표 + '검수권장'으로 알림
  4) 그래도 없으면 → 추정하지 않고 오류로 알린다 (어떤 칸을 못 찾았는지)
세목 줄 수는 양식에서 세어, 한 페이지에 넣을 줄 수(페이지 나누기 기준)로 쓴다.
"""
from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass, field

SECTION = "Contents/section0.xml"

# 기존(학복위 양식) 좌표 — (rowAddr, colAddr)
LEGACY_SCALAR: dict[str, tuple[int, int]] = {
    "사업번호": (1, 3),
    "사업명": (2, 3),
    "날짜": (3, 3),
    "수입지출": (3, 8),
    "구분": (3, 10),
    "내용": (4, 3),
    "총액": (26, 5),
    "비고": (27, 4),
}
LEGACY_DATA_ROWS = list(range(13, 26))
LEGACY_COLS = {"세목": 0, "수량": 2, "단가": 5}

FIELD_LABELS: dict[str, tuple[str, ...]] = {
    "사업번호": ("사업번호",),
    "사업명": ("사업명",),
    "날짜": ("날짜", "일자", "거래일자", "집행일자"),
    "수입지출": ("수입/지출", "수입·지출", "수입지출", "수입/지출구분", "수입,지출"),
    "구분": ("구분",),
    "내용": ("내용", "사업내용", "집행내용"),
    "총액": ("총액", "합계", "총계", "합계금액", "총금액"),
    "비고": ("비고",),
}
COLUMN_LABELS: dict[str, tuple[str, ...]] = {
    "세목": ("세목", "품목", "내역", "항목"),
    "수량": ("수량",),
    "단가": ("단가", "금액"),
}
ALL_LABEL_TEXTS = {t for labels in (*FIELD_LABELS.values(), *COLUMN_LABELS.values()) for t in labels}


class TemplateError(ValueError):
    """양식에서 꼭 필요한 칸을 찾지 못함 — 사용자가 고칠 수 있는 안내를 담는다."""


@dataclass(frozen=True)
class Cell:
    row: int
    col: int
    row_span: int
    col_span: int
    text: str  # 공백을 뺀 글자


@dataclass
class TemplateLayout:
    scalar: dict[str, tuple[int, int]]
    data_rows: list[int]
    semok_col: int
    qty_col: int
    price_col: int
    notes: list[str] = field(default_factory=list)  # 사용자에게 알릴 '검수권장'
    uses_legacy: bool = True

    @property
    def rows_per_page(self) -> int:
        return len(self.data_rows)

    def field_at(self) -> dict[tuple[int, int], str]:
        return {pos: name for name, pos in self.scalar.items()}


LEGACY_LAYOUT = TemplateLayout(
    scalar=dict(LEGACY_SCALAR),
    data_rows=list(LEGACY_DATA_ROWS),
    semok_col=LEGACY_COLS["세목"],
    qty_col=LEGACY_COLS["수량"],
    price_col=LEGACY_COLS["단가"],
)


def _norm(text: str) -> str:
    return re.sub(r"\s+", "", text)


def parse_cells(table_xml: str) -> list[Cell]:
    cells: list[Cell] = []
    for c in re.findall(r"<hp:tc\b.*?</hp:tc>", table_xml, re.S):
        addr = re.search(r'colAddr="(\d+)" rowAddr="(\d+)"', c)
        if not addr:
            continue
        cs = re.search(r'colSpan="(\d+)"', c)
        rs = re.search(r'rowSpan="(\d+)"', c)
        text = "".join(re.findall(r"<hp:t>([^<]*)</hp:t>", c))
        cells.append(Cell(
            row=int(addr.group(2)), col=int(addr.group(1)),
            row_span=int(rs.group(1)) if rs else 1, col_span=int(cs.group(1)) if cs else 1,
            text=_norm(text),
        ))
    return cells


def table_paragraph(xml: str) -> str:
    tm = re.search(r'(<hp:p\b[^>]*>(?:(?!</hp:p>).)*?<hp:tbl\b.*?</hp:tbl>.*?</hp:p>)', xml, re.S)
    if not tm:
        raise TemplateError("양식에서 표를 찾지 못했습니다. 수입지출내역서 표가 들어 있는 hwpx인지 확인해 주세요.")
    return tm.group(1)


class _Grid:
    def __init__(self, cells: list[Cell]):
        self.cells = cells
        self.at = {(c.row, c.col): c for c in cells}

    def left_of(self, row: int, col: int) -> Cell | None:
        for c in self.cells:
            if c.col + c.col_span == col and c.row <= row < c.row + c.row_span:
                return c
        return None

    def right_of(self, cell: Cell) -> Cell | None:
        return self.at.get((cell.row, cell.col + cell.col_span))

    def find_labels(self, labels: tuple[str, ...]) -> list[Cell]:
        wanted = {_norm(l) for l in labels}
        return [c for c in self.cells if c.text in wanted]


def _legacy_scalar_ok(grid: _Grid, name: str) -> bool:
    pos = LEGACY_SCALAR[name]
    if pos not in grid.at:
        return False
    left = grid.left_of(*pos)
    return left is not None and left.text in {_norm(l) for l in FIELD_LABELS[name]}


def _find_scalar(grid: _Grid, name: str, taken: set[tuple[int, int]]) -> tuple[int, int] | None:
    for label in grid.find_labels(FIELD_LABELS[name]):
        target = grid.right_of(label)
        if target and (target.row, target.col) not in taken and target.text not in ALL_LABEL_TEXTS:
            return target.row, target.col
    return None


def _legacy_rows_ok(grid: _Grid) -> bool:
    header = LEGACY_DATA_ROWS[0] - 1
    for key, col in LEGACY_COLS.items():
        cell = grid.at.get((header, col))
        if cell is None or cell.text not in {_norm(l) for l in COLUMN_LABELS[key]}:
            return False
    return all((r, c) in grid.at for r in LEGACY_DATA_ROWS for c in LEGACY_COLS.values())


def _legacy_rows_exist(grid: _Grid) -> bool:
    return all((r, c) in grid.at for r in LEGACY_DATA_ROWS for c in LEGACY_COLS.values())


def _find_rows(grid: _Grid) -> tuple[list[int], int, int, int] | None:
    for semok in grid.find_labels(COLUMN_LABELS["세목"]):
        same_row = [c for c in grid.cells if c.row == semok.row]
        qty = next((c for c in same_row if c.text in COLUMN_LABELS["수량"]), None)
        price = next((c for c in same_row if c.text in {_norm(l) for l in COLUMN_LABELS["단가"]}), None)
        if not qty or not price:
            continue
        rows: list[int] = []
        r = semok.row + semok.row_span
        while True:
            cell = grid.at.get((r, semok.col))
            if cell is None or cell.text in ALL_LABEL_TEXTS:
                break
            if (r, qty.col) not in grid.at or (r, price.col) not in grid.at:
                break
            rows.append(r)
            r += cell.row_span
        if rows:
            return rows, semok.col, qty.col, price.col
    return None


def analyze_table(table_xml: str) -> TemplateLayout:
    grid = _Grid(parse_cells(table_xml))
    notes: list[str] = []
    missing: list[str] = []
    scalar: dict[str, tuple[int, int]] = {}
    uses_legacy = True

    # 세목 줄
    if _legacy_rows_ok(grid):
        rows, sc, qc, pc = list(LEGACY_DATA_ROWS), LEGACY_COLS["세목"], LEGACY_COLS["수량"], LEGACY_COLS["단가"]
    else:
        found = _find_rows(grid)
        if found:
            rows, sc, qc, pc = found
            uses_legacy = False
        elif _legacy_rows_exist(grid):
            rows, sc, qc, pc = list(LEGACY_DATA_ROWS), LEGACY_COLS["세목"], LEGACY_COLS["수량"], LEGACY_COLS["단가"]
            notes.append("양식에서 '세목·수량·단가' 머리줄을 확인하지 못해 기존 위치(14~26행)에 채웠습니다.")
        else:
            rows, sc, qc, pc = [], 0, 0, 0
            missing.append("세목·수량·단가 표")
    data_cells = {(r, c) for r in rows for c in (sc, qc, pc)}

    # 나머지 칸
    for name in LEGACY_SCALAR:
        taken = set(scalar.values()) | data_cells
        if _legacy_scalar_ok(grid, name) and LEGACY_SCALAR[name] not in taken:
            scalar[name] = LEGACY_SCALAR[name]
            continue
        pos = _find_scalar(grid, name, taken)
        if pos:
            scalar[name] = pos
            uses_legacy = False
            continue
        if LEGACY_SCALAR[name] in grid.at and LEGACY_SCALAR[name] not in taken:
            scalar[name] = LEGACY_SCALAR[name]
            notes.append(f"양식에서 '{name}' 라벨을 확인하지 못해 기존 위치에 채웠습니다.")
            continue
        missing.append(name)

    if missing:
        raise TemplateError(
            "양식에서 " + ", ".join(f"'{m}'" for m in missing) + " 칸을 찾지 못했습니다. "
            "양식 표에 해당 라벨(예: 사업번호, 날짜, 세목·수량·단가, 총액, 비고)이 있는지, "
            "값을 넣을 칸이 라벨 바로 오른쪽에 있는지 확인해 주세요."
        )
    return TemplateLayout(
        scalar=scalar, data_rows=rows, semok_col=sc, qty_col=qc, price_col=pc,
        notes=notes, uses_legacy=uses_legacy,
    )


def analyze_template(template_path: str) -> TemplateLayout:
    try:
        with zipfile.ZipFile(template_path) as z:
            xml = z.read(SECTION).decode("utf-8")
    except (KeyError, zipfile.BadZipFile) as exc:
        raise TemplateError("양식 파일을 열지 못했습니다. 한글에서 hwpx로 저장한 파일인지 확인해 주세요.") from exc
    return analyze_table(table_paragraph(xml))
