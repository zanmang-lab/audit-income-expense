"""
fill_hwpx.py — 수입지출내역서 양식(.hwpx)을 채운다.

설계 요지
- 양식은 표 1개. 값 칸 위치는 src/template_layout.py가 정한다
  (기존 학복위 좌표를 라벨로 확인 → 다른 양식은 라벨 오른쪽 칸).
- 빈 셀의 <hp:run charPrIDRef="N"/> (빈 런)에 <hp:t>값</hp:t>을 끼워 넣어 글꼴/정렬 보존.
- 페이지 1개 = 표 1벌. 2페이지부터 표 문단에 pageBreak="1" 부여.
- 세목 줄 수는 양식에서 센다(학복위 양식 13줄). 엔진이 그 줄 수로 페이지를 나눈다.

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

from .template_layout import (
    LEGACY_LAYOUT,
    TemplateLayout,
    analyze_table,
    parse_cells,
    table_paragraph,
)

# 기존 학복위 양식 좌표 (하위 호환용 이름). 실제 위치는 양식마다 template_layout이 정한다.
SCALAR_CELLS = LEGACY_LAYOUT.field_at()
DATA_ROWS = LEGACY_LAYOUT.data_rows
SEMOK_COL, QTY_COL, PRICE_COL = LEGACY_LAYOUT.semok_col, LEGACY_LAYOUT.qty_col, LEGACY_LAYOUT.price_col
MULTILINE_FIELDS = {"비고"}

# 좌표 대신 '라벨 오른쪽 칸'으로 찾는 작성자 정보 (양식마다 위치가 달라도 동작)
LABEL_FIELDS: dict[str, tuple[str, ...]] = {
    "성명": ("성명", "작성자"),
    "직위": ("직위", "직책"),
    "소속": ("소속",),
    "연락처": ("연락처", "전화번호", "연락처(전화)"),
}


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

    updated = re.sub(
        r'<hp:run charPrIDRef="\d+"><hp:t(?:></hp:t>|/>)</hp:run>',
        repl_empty_t,
        cell_xml,
        count=1,
    )
    if updated != cell_xml:
        return updated

    # 빈 run이 아예 없는 칸: 첫 문단 맨 앞에 run을 넣는다(글자 모양은 문단 기본).
    if re.search(r"<hp:t>[^<]+</hp:t>", cell_xml):
        return cell_xml  # 이미 글자가 있는 칸은 건드리지 않는다
    return re.sub(
        r"(<hp:p\b[^>]*>)",
        lambda m: m.group(1) + _runs_for_text("0", text, multiline),
        cell_xml,
        count=1,
    )


def _replace_cell_text(cell_xml: str, text: str) -> str:
    """칸에 글자가 이미 있으면 바꾸고, 비어 있으면 채운다(양식에 예전 작성자가 적혀 있는 경우)."""
    if not re.search(r"<hp:t>[^<]+</hp:t>", cell_xml):
        return _set_cell_text(cell_xml, text)
    done = False

    def repl(m):
        nonlocal done
        if done:
            return "<hp:t></hp:t>"
        done = True
        return f"<hp:t>{_esc(text)}</hp:t>"

    return re.sub(r"<hp:t>[^<]*</hp:t>|<hp:t/>", repl, cell_xml)


def find_label_targets(
    table_xml: str,
    fields: dict[str, tuple[str, ...]] = LABEL_FIELDS,
    layout: TemplateLayout | None = None,
) -> dict[str, tuple[int, int]]:
    """라벨 칸(예: '성명') 바로 오른쪽 칸의 좌표를 찾는다. 내역서 값 칸과 겹치면 쓰지 않는다."""
    layout = layout or LEGACY_LAYOUT
    reserved = set(layout.scalar.values())
    data_rows = set(layout.data_rows)
    cells = parse_cells(table_xml)
    at = {(c.row, c.col) for c in cells}
    targets: dict[str, tuple[int, int]] = {}
    label_cells: dict[str, object] = {}
    for field, labels in fields.items():
        for c in cells:
            if c.text in labels:
                if c.row not in data_rows:
                    label_cells[field] = c
                break
    # 1) 라벨과 같은 줄에서 시작하는 오른쪽 칸
    for field, c in label_cells.items():
        pos = (c.row, c.col + c.col_span)
        if pos in at and pos not in reserved:
            targets[field] = pos
    # 2) 26-1 학복위 양식처럼 오른쪽 칸이 라벨과 한 줄 어긋나 병합된 경우:
    #    라벨 줄과 겹치는 오른쪽 칸 중 아직 안 쓴 가장 위 칸
    used = set(targets.values())
    for field, c in label_cells.items():
        if field in targets:
            continue
        label_rows = range(c.row, c.row + c.row_span)
        for cand in sorted(cells, key=lambda x: x.row):
            pos = (cand.row, cand.col)
            if (cand.col == c.col + c.col_span and pos not in reserved and pos not in used
                    and cand.row not in data_rows and not cand.text
                    and any(r in label_rows for r in range(cand.row, cand.row + cand.row_span))):
                targets[field] = pos
                used.add(pos)
                break
    return targets


EVIDENCE_LABELS = ("증빙서류", "증빙", "증빙자료")


def find_evidence_target(table_xml: str, layout: TemplateLayout | None = None) -> tuple[int, int] | None:
    """'증빙서류' 라벨 아래 칸(26-1 양식), 없으면 오른쪽 칸의 좌표."""
    layout = layout or LEGACY_LAYOUT
    reserved = set(layout.scalar.values())
    data_rows = set(layout.data_rows)
    cells = parse_cells(table_xml)
    at = {(c.row, c.col): c for c in cells}
    for c in cells:
        if c.text in EVIDENCE_LABELS:
            below = at.get((c.row + c.row_span, c.col))
            if below and not below.text and below.col_span == c.col_span and (below.row, below.col) not in reserved:
                return (below.row, below.col)
            right = at.get((c.row, c.col + c.col_span))
            if right and not right.text and (right.row, right.col) not in reserved and right.row not in data_rows:
                return (right.row, right.col)
            return None
    return None


def _pic_xml(item_id: str, cell_xml: str, img, serial: int) -> str:
    """칸 크기에 맞춘 글자처럼 취급하는 그림(<hp:pic>)."""
    def num(pattern: str, default: int) -> int:
        m = re.search(pattern, cell_xml)
        return int(m.group(1)) if m else default
    cell_w = num(r'<hp:cellSz width="(\d+)"', 30000)
    cell_h = num(r'<hp:cellSz width="\d+" height="(\d+)"', 30000)
    mx = num(r'<hp:cellMargin left="(\d+)"', 510) + num(r'<hp:cellMargin left="\d+" right="(\d+)"', 510)
    my = num(r'<hp:cellMargin[^>]*top="(\d+)"', 141) + num(r'<hp:cellMargin[^>]*bottom="(\d+)"', 141)
    box_w, box_h = max(cell_w - mx - 200, 1000), max(cell_h - my - 200, 1000)
    w = box_w
    h = round(w * img.height_px / img.width_px)
    if h > box_h:
        h = box_h
        w = round(h * img.width_px / img.height_px)
    pid = 1900000000 + serial
    return (
        f'<hp:pic textWrap="SQUARE" textFlow="BOTH_SIDES" reverse="0" id="{pid}" zOrder="{serial}" '
        f'numberingType="PICTURE" lock="0" dropcapstyle="None" href="" groupLevel="0" instid="{pid}">'
        f'<hp:offset x="0" y="0"/><hp:orgSz width="{w}" height="{h}"/><hp:curSz width="{w}" height="{h}"/>'
        f'<hp:flip horizontal="0" vertical="0"/>'
        f'<hp:rotationInfo angle="0" centerX="{w // 2}" centerY="{h // 2}" rotateimage="1"/>'
        '<hp:renderingInfo><hc:transMatrix e1="1" e2="0" e3="0" e4="0" e5="1" e6="0"/>'
        '<hc:scaMatrix e1="1" e2="0" e3="0" e4="0" e5="1" e6="0"/>'
        '<hc:rotMatrix e1="1" e2="0" e3="0" e4="0" e5="1" e6="0"/></hp:renderingInfo>'
        f'<hp:imgRect><hc:pt0 x="0" y="0"/><hc:pt1 x="{w}" y="0"/><hc:pt2 x="{w}" y="{h}"/><hc:pt3 x="0" y="{h}"/></hp:imgRect>'
        f'<hp:imgClip left="0" right="{w}" top="0" bottom="{h}"/>'
        '<hp:inMargin left="0" right="0" top="0" bottom="0"/>'
        f'<hp:imgDim dimwidth="{w}" dimheight="{h}"/>'
        f'<hc:img binaryItemIDRef="{item_id}" bright="0" contrast="0" effect="REAL_PIC" alpha="0"/>'
        f'<hp:effects/><hp:sz width="{w}" height="{h}" widthRelTo="ABSOLUTE" heightRelTo="ABSOLUTE" protect="0"/>'
        '<hp:pos treatAsChar="1" affectLSpacing="0" flowWithText="1" allowOverlap="0" holdAnchorAndSO="0" '
        'vertRelTo="PARA" horzRelTo="COLUMN" vertAlign="TOP" horzAlign="LEFT" vertOffset="0" horzOffset="0"/>'
        '<hp:outMargin left="0" right="0" top="0" bottom="0"/><hp:shapeComment/></hp:pic>'
    )


def _put_picture(cell_xml: str, pic: str) -> str:
    if re.search(r"<hp:t>[^<]+</hp:t>|<hp:pic\b", cell_xml):
        return cell_xml  # 이미 무언가 있는 칸은 건드리지 않는다
    updated, n = re.subn(r'<hp:run charPrIDRef="(\d+)"\s*/>', lambda m: f'<hp:run charPrIDRef="{m.group(1)}">{pic}</hp:run>', cell_xml, count=1)
    if n:
        return updated
    return re.sub(r"(<hp:p\b[^>]*>)", lambda m: m.group(1) + f'<hp:run charPrIDRef="0">{pic}</hp:run>', cell_xml, count=1)


def _register_image(work: str, img) -> str:
    """BinData에 그림을 넣고 content.hpf 매니페스트에 등록. 항목 id를 돌려준다."""
    hpf_path = os.path.join(work, "Contents", "content.hpf")
    hpf = open(hpf_path, encoding="utf-8").read() if os.path.exists(hpf_path) else ""
    n = 1
    while f'id="EVI{n:04d}"' in hpf or os.path.exists(os.path.join(work, "BinData", f"EVI{n:04d}.{img.ext}")):
        n += 1
    item_id = f"EVI{n:04d}"
    os.makedirs(os.path.join(work, "BinData"), exist_ok=True)
    with open(os.path.join(work, "BinData", f"{item_id}.{img.ext}"), "wb") as f:
        f.write(img.data)
    media = "image/jpeg" if img.ext == "jpg" else "image/png"
    # 한/글이 저장한 hwpx처럼 매니페스트에만 등록한다(isEmbeded="1"이 없으면 그림이 빠진다).
    item = f'<opf:item id="{item_id}" href="BinData/{item_id}.{img.ext}" media-type="{media}" isEmbeded="1"/>'
    if "</opf:manifest>" in hpf:
        hpf = hpf.replace("</opf:manifest>", item + "</opf:manifest>", 1)
        with open(hpf_path, "w", encoding="utf-8") as f:
            f.write(hpf)
    return item_id


def missing_label_fields(template_path: str, fields: list[str], layout: TemplateLayout | None = None) -> list[str]:
    """양식에서 라벨을 찾지 못한 작성자 정보 항목."""
    with zipfile.ZipFile(template_path) as z:
        xml = z.read(SECTION).decode("utf-8")
    targets = find_label_targets(table_paragraph(xml), layout=layout)
    return [f for f in fields if f not in targets]


def _cell_addr(cell_xml: str):
    m = re.search(r'colAddr="(\d+)" rowAddr="(\d+)"', cell_xml)
    return (int(m.group(2)), int(m.group(1))) if m else (None, None)


def _fill_table(
    table_para_xml: str,
    page: dict,
    label_targets: dict | None = None,
    layout: TemplateLayout | None = None,
    problems: list[str] | None = None,
    picture: tuple[tuple[int, int], str, object, int] | None = None,
) -> str:
    layout = layout or LEGACY_LAYOUT
    scalar_at = layout.field_at()
    data_rows = layout.data_rows
    cells = re.findall(r'<hp:tc\b.*?</hp:tc>', table_para_xml, re.S)
    out = table_para_xml
    rows = page.get("rows", [])
    by_pos = {pos: field for field, pos in (label_targets or {}).items()}

    def put(cell_xml: str, value: str, what: str, multiline: bool = False) -> str | None:
        new_c = _set_cell_text(cell_xml, value, multiline=multiline)
        if new_c == cell_xml:
            if problems is not None:
                problems.append(f"{page.get('사업번호', '')} 페이지 '{what}' 칸에 값을 넣지 못했습니다(칸 모양이 예상과 다름).")
            return None
        return new_c

    for c in cells:
        r, col = _cell_addr(c)
        new_c = None
        if picture and (r, col) == picture[0]:
            _, item_id, img, serial = picture
            new_c = _put_picture(c, _pic_xml(item_id, c, img, serial))
        elif (r, col) in by_pos:
            val = page.get(by_pos[(r, col)], "")
            if val:
                new_c = _replace_cell_text(c, val)
        elif (r, col) in scalar_at:
            field = scalar_at[(r, col)]
            val = page.get(field, "")
            if val != "":
                new_c = put(c, val, field, multiline=field in MULTILINE_FIELDS)
        elif r in data_rows:
            idx = data_rows.index(r)
            if idx < len(rows):
                semok, qty, price = rows[idx]
                if col == layout.semok_col:
                    new_c = put(c, semok, "세목")
                elif col == layout.qty_col:
                    new_c = put(c, qty, "수량")
                elif col == layout.price_col:
                    new_c = put(c, price, "단가")
        if new_c:
            out = out.replace(c, new_c, 1)
    return out


def _build_section_xml(
    template_dir: str,
    pages: list,
    layout: TemplateLayout | None = None,
    problems: list[str] | None = None,
    evidence_image=None,
) -> str:
    xml = open(os.path.join(template_dir, SECTION), encoding="utf-8").read()
    # 표를 감싼 문단 <hp:p ...> ... <hp:tbl>...</hp:tbl> ... </hp:p> 통째 추출
    table_para = table_paragraph(xml)
    layout = layout or analyze_table(table_para)
    label_targets = find_label_targets(table_para, layout=layout)
    item_id, evidence_pos = None, None
    if evidence_image is not None and any(pg.get("증빙이미지") for pg in pages):
        evidence_pos = find_evidence_target(table_para, layout)
        if evidence_pos is None:
            if problems is not None:
                problems.append("양식에서 '증빙서류' 칸을 찾지 못해 물품 대여 증빙 이미지를 넣지 못했습니다.")
        else:
            item_id = _register_image(template_dir, evidence_image)
    blocks = []
    for i, pg in enumerate(pages):
        if len(pg.get("rows", [])) > layout.rows_per_page:
            raise ValueError(
                f"페이지 '{pg.get('사업번호', '')}' 세목 {len(pg['rows'])}줄 — "
                f"양식 세목 칸은 {layout.rows_per_page}줄입니다."
            )
        picture = (evidence_pos, item_id, evidence_image, i + 1) if item_id and pg.get("증빙이미지") else None
        b = _fill_table(table_para, pg, label_targets, layout, problems, picture)
        if i > 0:
            b = b.replace('pageBreak="0"', 'pageBreak="1"', 1)  # 2페이지부터 페이지나눔
        blocks.append(b)
    return xml.replace(table_para, "".join(blocks), 1)


def fill_document(
    template_path: str,
    pages: list,
    out_path: str,
    layout: TemplateLayout | None = None,
    problems: list[str] | None = None,
    evidence_image=None,
):
    """template_path(.hwpx)를 pages로 채워 out_path(.hwpx)로 저장.

    evidence_image(src.evidence_image.EvidenceImage)가 있으면 '증빙이미지'가 참인 쪽의
    증빙서류 칸에 그림을 넣는다.

    layout을 주지 않으면 양식에서 칸 위치를 찾는다(src/template_layout.py).
    값을 넣지 못한 칸은 problems에 남긴다.
    """
    work = out_path + "__work"
    if os.path.exists(work):
        shutil.rmtree(work)
    os.makedirs(work)
    try:
        with zipfile.ZipFile(template_path) as z:
            z.extractall(work)
        new_xml = _build_section_xml(work, pages, layout, problems, evidence_image)
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
