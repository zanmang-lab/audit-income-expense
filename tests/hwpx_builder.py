"""테스트용 수입지출내역서 양식(.hwpx) 생성기.

standard_cells()는 학복위 양식 좌표(사업번호 1행 3열, 세목 13~25행, 총액 26행, 비고 27행)와
같은 구조다. shift로 위에 줄을 끼우거나, data_rows로 세목 줄 수를 바꾼 변형 양식을 만든다.
"""
from __future__ import annotations

import io
import zipfile
from pathlib import Path

Cell = tuple[int, int, int, int, str]  # row, col, rowspan, colspan, text


def standard_cells(shift: int = 0, data_rows: int = 13, labels: dict[str, str] | None = None) -> list[Cell]:
    lab = {"사업번호": "사업번호", "날짜": "날짜", "수입지출": "수입/지출", "총액": "총 액", "세목": "세 목", **(labels or {})}
    s = shift
    cells: list[Cell] = []
    if shift:
        cells.append((0, 0, shift, 12, "단체명: 테스트 위원회"))
    cells += [
        (s + 0, 0, 1, 12, "수 입 지 출 내 역 서"),
        (s + 1, 0, 1, 3, lab["사업번호"]), (s + 1, 3, 1, 9, ""),
        (s + 2, 0, 1, 3, "사업명"), (s + 2, 3, 1, 9, ""),
        (s + 3, 0, 1, 3, lab["날짜"]), (s + 3, 3, 1, 4, ""), (s + 3, 7, 1, 1, lab["수입지출"]), (s + 3, 8, 1, 1, ""),
        (s + 3, 9, 1, 1, "구분"), (s + 3, 10, 1, 2, ""),
        (s + 4, 0, 1, 3, "내용"), (s + 4, 3, 1, 9, ""),
        (s + 5, 0, 1, 3, "성명"), (s + 5, 3, 1, 3, ""), (s + 5, 6, 1, 1, "직위"), (s + 5, 7, 1, 5, ""),
        (s + 6, 0, 1, 3, "소속"), (s + 6, 3, 1, 3, ""), (s + 6, 6, 1, 1, "증빙서류"), (s + 6, 7, 1, 5, ""),
        (s + 7, 0, 1, 3, "연락처"), (s + 7, 3, 1, 9, ""),
    ]
    for r in range(8, 12):
        cells.append((s + r, 0, 1, 12, ""))
    h = s + 12
    cells += [(h, 0, 1, 2, lab["세목"]), (h, 2, 1, 3, "수량"), (h, 5, 1, 7, "단가")]
    for i in range(data_rows):
        r = h + 1 + i
        cells += [(r, 0, 1, 2, ""), (r, 2, 1, 3, ""), (r, 5, 1, 7, "")]
    t = h + 1 + data_rows
    cells += [(t, 0, 1, 5, lab["총액"]), (t, 5, 1, 7, ""), (t + 1, 0, 1, 4, "비 고"), (t + 1, 4, 1, 8, "")]
    return cells


def _cell_xml(row, col, rowspan, colspan, text) -> str:
    run = f'<hp:run charPrIDRef="1"><hp:t>{text}</hp:t></hp:run>' if text else '<hp:run charPrIDRef="1"/>'
    return (
        '<hp:tc><hp:subList><hp:p paraPrIDRef="0" styleIDRef="0" pageBreak="0">'
        f"{run}</hp:p></hp:subList>"
        f'<hp:cellAddr colAddr="{col}" rowAddr="{row}"/><hp:cellSpan colSpan="{colspan}" rowSpan="{rowspan}"/></hp:tc>'
    )


def build_hwpx(cells: list[Cell]) -> bytes:
    rows: dict[int, list[str]] = {}
    for c in cells:
        rows.setdefault(c[0], []).append(_cell_xml(*c))
    body = "".join(f"<hp:tr>{''.join(v)}</hp:tr>" for _, v in sorted(rows.items()))
    xml = (
        '<hs:sec><hp:p id="0" paraPrIDRef="0" styleIDRef="0" pageBreak="0"><hp:run charPrIDRef="0"><hp:tbl>'
        f"{body}</hp:tbl></hp:run></hp:p></hs:sec>"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("mimetype", "application/hwp+zip", compress_type=zipfile.ZIP_STORED)
        z.writestr("Contents/section0.xml", xml)
    return buf.getvalue()


def write_hwpx(path: Path, cells: list[Cell]) -> Path:
    path.write_bytes(build_hwpx(cells))
    return path


def read_cells(path_or_bytes) -> list[dict[tuple[int, int], str]]:
    """결과 hwpx를 페이지(표)별 {(row, col): 글자} 로."""
    import re

    data = path_or_bytes if isinstance(path_or_bytes, bytes) else Path(path_or_bytes).read_bytes()
    x = zipfile.ZipFile(io.BytesIO(data)).read("Contents/section0.xml").decode()
    pages = []
    for t in re.findall(r"<hp:tbl\b.*?</hp:tbl>", x, re.S):
        cells = {}
        for c in re.findall(r"<hp:tc\b.*?</hp:tc>", t, re.S):
            m = re.search(r'colAddr="(\d+)" rowAddr="(\d+)"', c)
            cells[(int(m.group(2)), int(m.group(1)))] = "/".join(re.findall(r"<hp:t>([^<]*)</hp:t>", c))
        pages.append(cells)
    return pages
