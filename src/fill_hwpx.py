"""
fill_hwpx.py — 수입지출내역서 양식(.hwpx)을 좌표 기반으로 채운다.

설계 요지
- 양식은 표 1개. 값 셀은 (rowAddr, colAddr)로 고정 식별 → 토큰 불필요.
- 빈 셀의 <hp:run charPrIDRef="N"/> (빈 런)에 <hp:t>값</hp:t>을 끼워 넣어 글꼴/정렬 보존.
- 페이지 1개 = 표 1벌. 2페이지부터 표 문단에 pageBreak="1" 부여.
- 세목 행은 13줄 고정. 엔진이 페이지당 ≤13줄로 분할하므로 행 복제 불필요(부족분은 공백 유지).

엔진 연동
- fill_document(template_path, pages, out_path) 호출.
- pages: 페이지 dict 리스트. 각 dict:
    {
      "사업번호": "2-1~13", "사업명": "...", "날짜": "2026.03.10.",
      "수입지출": "수입", "구분": "지출의환급", "내용": "...",
      "총액": "52,000", "비고": "여러 줄은 \\n으로",
      "rows": [("세목","1","4,000"), ...]   # 최대 13개
    }
- 사업명별로 pages를 묶어 파일을 나누는 것은 main.py(호출부)에서 처리.
"""
import os, re, shutil, zipfile
from pathlib import Path

SECTION = "Contents/section0.xml"

# (rowAddr, colAddr) → 필드명
SCALAR_CELLS = {
    (1, 3): "사업번호",
    (2, 3): "사업명",
    (3, 3): "날짜",
    (3, 8): "수입지출",
    (3, 10): "구분",
    (4, 3): "내용",
    (26, 5): "총액",
    (27, 4): "비고",
}
# 증빙서류 값 셀 (6,7)은 항상 공백 → 매핑하지 않음
DATA_ROWS = list(range(13, 26))        # rowAddr 13~25 = 13줄
SEMOK_COL, QTY_COL, PRICE_COL = 0, 2, 5
MULTILINE_FIELDS = {"비고"}


def _esc(s: str) -> str:
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _runs_for_text(char_pr_ref: str, value: str, multiline: bool) -> str:
    if not multiline or "\n" not in value:
        return f'<hp:run charPrIDRef="{char_pr_ref}"><hp:t>{_esc(value)}</hp:t></hp:run>'
    parts = value.split("\n")
    chunks: list[str] = []
    for i, part in enumerate(parts):
        if i > 0:
            chunks.append("<hp:lineBreak/>")
        chunks.append(
            f'<hp:run charPrIDRef="{char_pr_ref}"><hp:t>{_esc(part)}</hp:t></hp:run>'
        )
    return "".join(chunks)


def _set_cell_multiline_paragraphs(cell_xml: str, text: str) -> str | None:
    """셀 안에 줄마다 hp:p 문단을 넣어 한글에서 줄바꿈이 유지되게 한다."""
    sub_match = re.search(r"(<hp:subList\b[^>]*>)(.*?)(</hp:subList>)", cell_xml, re.S)
    if not sub_match:
        return None

    p_match = re.search(r"(<hp:p\b[^>]*>)(.*?)(</hp:p>)", sub_match.group(2), re.S)
    if not p_match:
        return None

    p_open, p_inner, p_close = p_match.groups()
    ref_match = re.search(r'charPrIDRef="(\d+)"', p_inner)
    char_pr_ref = ref_match.group(1) if ref_match else "0"

    paragraphs: list[str] = []
    for line in text.split("\n"):
        paragraphs.append(
            f"{p_open}<hp:run charPrIDRef=\"{char_pr_ref}\">"
            f"<hp:t>{_esc(line)}</hp:t></hp:run>{p_close}"
        )

    new_sub = sub_match.group(1) + "".join(paragraphs) + sub_match.group(3)
    return cell_xml.replace(sub_match.group(0), new_sub, 1)


def _set_cell_text(cell_xml: str, text: str, multiline: bool = False) -> str:
    """빈 런을 텍스트 든 런으로 치환. 줄바꿈은 hp:p 문단 분리 우선."""
    if multiline and "\n" in text:
        multiline_cell = _set_cell_multiline_paragraphs(cell_xml, text)
        if multiline_cell is not None:
            return multiline_cell

    def repl(m):
        ref_match = re.search(r'charPrIDRef="(\d+)"', m.group(0))
        char_pr_ref = ref_match.group(1) if ref_match else "0"
        return _runs_for_text(char_pr_ref, text, multiline)

    updated = re.sub(r'<hp:run charPrIDRef="\d+"/>', repl, cell_xml, count=1)
    if updated != cell_xml:
        return updated

    def repl_empty_t(m):
        ref_match = re.search(r'charPrIDRef="(\d+)"', m.group(0))
        char_pr_ref = ref_match.group(1) if ref_match else "0"
        return _runs_for_text(char_pr_ref, text, multiline)

    return re.sub(
        r'<hp:run charPrIDRef="\d+"><hp:t></hp:t></hp:run>',
        repl_empty_t,
        cell_xml,
        count=1,
    )


def _cell_addr(cell_xml: str):
    m = re.search(r'colAddr="(\d+)" rowAddr="(\d+)"', cell_xml)
    return (int(m.group(2)), int(m.group(1))) if m else (None, None)


def _fill_table(table_para_xml: str, page: dict) -> str:
    cells = re.findall(r'<hp:tc\b.*?</hp:tc>', table_para_xml, re.S)
    out = table_para_xml
    rows = page.get("rows", [])
    for c in cells:
        r, col = _cell_addr(c)
        new_c = None
        if (r, col) in SCALAR_CELLS:
            field = SCALAR_CELLS[(r, col)]
            val = page.get(field, "")
            if val != "":
                new_c = _set_cell_text(c, val, multiline=field in MULTILINE_FIELDS)
        elif r in DATA_ROWS:
            idx = DATA_ROWS.index(r)
            if idx < len(rows):
                semok, qty, price = rows[idx]
                if col == SEMOK_COL:
                    new_c = _set_cell_text(c, semok)
                elif col == QTY_COL:
                    new_c = _set_cell_text(c, qty)
                elif col == PRICE_COL:
                    new_c = _set_cell_text(c, price)
        if new_c:
            out = out.replace(c, new_c, 1)
    return out


def _build_section_xml(template_dir: str, pages: list) -> str:
    xml = open(os.path.join(template_dir, SECTION), encoding="utf-8").read()
    # 표를 감싼 문단 <hp:p ...> ... <hp:tbl>...</hp:tbl> ... </hp:p> 통째 추출
    tm = re.search(r'(<hp:p\b[^>]*>(?:(?!</hp:p>).)*?<hp:tbl\b.*?</hp:tbl>.*?</hp:p>)', xml, re.S)
    if not tm:
        raise RuntimeError("양식에서 표 문단을 찾지 못했습니다.")
    table_para = tm.group(1)
    blocks = []
    for i, pg in enumerate(pages):
        b = _fill_table(table_para, pg)
        if i > 0:
            b = b.replace('pageBreak="0"', 'pageBreak="1"', 1)  # 2페이지부터 페이지나눔
        blocks.append(b)
    return xml.replace(table_para, "".join(blocks), 1)


def fill_document(template_path: str, pages: list, out_path: str):
    """template_path(.hwpx)를 pages로 채워 out_path(.hwpx)로 저장."""
    work = out_path + "__work"
    if os.path.exists(work):
        shutil.rmtree(work)
    os.makedirs(work)
    try:
        with zipfile.ZipFile(template_path) as z:
            z.extractall(work)
        new_xml = _build_section_xml(work, pages)
        with open(os.path.join(work, SECTION), "w", encoding="utf-8") as f:
            f.write(new_xml)
        if os.path.exists(out_path):
            os.remove(out_path)
        # mimetype은 반드시 첫 항목 + 무압축(STORED) 이어야 한글이 연다
        with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as z:
            mt = os.path.join(work, "mimetype")
            if os.path.exists(mt):
                z.write(mt, "mimetype", compress_type=zipfile.ZIP_STORED)
            for root, _, files in os.walk(work):
                for fn in files:
                    full = os.path.join(root, fn)
                    rel = os.path.relpath(full, work)
                    if rel == "mimetype":
                        continue
                    z.write(full, rel)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return out_path


if __name__ == "__main__":
    demo = [
        {"사업번호": "2-1~13", "사업명": "1학기대면사물함배부사업", "날짜": "2026.03.10.",
         "수입지출": "수입", "구분": "지출의환급", "내용": "중앙도서관 사물함 수익금 입금",
         "총액": "8,000",
         "비고": "전자 사물함은 대여비 4,000원, 일반 사물함은 대여비 2,000원",
         "rows": [("전자49장문경", "1", "4,000"), ("전자26곽도윤", "1", "4,000")]},
        {"사업번호": "3-1", "사업명": "1학기물품대여사업", "날짜": "2026.03.23.",
         "수입지출": "수입", "구분": "지출의환급", "내용": "대여 물품 연체료 입금",
         "총액": "4,000",
         "비고": "김지민님 보조배터리 1개 대여 후 4일 연체되어 연체료 4,000원 부과",
         "rows": [("김지민", "1", "4,000")]},
    ]
    root = Path(__file__).resolve().parent.parent
    tpl = root / "templates" / "수입지출내역서_양식.hwpx"
    out = root / "output" / "_sample.hwpx"
    fill_document(str(tpl), demo, str(out))
    print(f"샘플 생성 완료: {out}")
