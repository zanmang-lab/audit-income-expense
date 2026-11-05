"""3단계(양식 유연화) 테스트."""
from __future__ import annotations

import re
from datetime import date

import pytest

from hwpx_builder import build_hwpx, read_cells, standard_cells, write_hwpx
from src.fill_hwpx import fill_document
from src.parse_ledger import LedgerRow, split_pages
from src.template_layout import LEGACY_SCALAR, TemplateError, analyze_template

PAGE = {
    "사업번호": "1-1~2", "사업명": "사물함", "날짜": "2026.03.10.", "수입지출": "수입", "구분": "사업수입",
    "내용": "입금", "총액": "8,000", "비고": "첫 줄\n둘째 줄", "rows": [("김", "1", "4,000"), ("이", "1", "4,000")],
}


def _layout(tmp_path, cells):
    return analyze_template(str(write_hwpx(tmp_path / "t.hwpx", cells)))


def test_standard_template_uses_legacy_positions(tmp_path):
    layout = _layout(tmp_path, standard_cells())
    assert layout.uses_legacy and not layout.notes
    assert layout.scalar == LEGACY_SCALAR
    assert layout.rows_per_page == 13


def test_shifted_template_found_by_labels(tmp_path):
    tpl = write_hwpx(tmp_path / "t.hwpx", standard_cells(shift=2))
    layout = analyze_template(str(tpl))
    assert not layout.uses_legacy
    assert layout.scalar["사업번호"] == (3, 3) and layout.scalar["비고"] == (29, 4)
    assert layout.data_rows == list(range(15, 28))

    out = tmp_path / "o.hwpx"
    fill_document(str(tpl), [PAGE], str(out))
    cells = read_cells(out)[0]
    assert cells[(3, 3)] == "1-1~2" and cells[(5, 8)] == "수입" and cells[(28, 5)] == "8,000"
    assert cells[(15, 0)] == "김" and cells[(16, 5)] == "4,000"
    assert cells[(29, 4)] == "첫 줄/둘째 줄"
    assert (1, 3) not in cells  # 예전 좌표 칸은 이 양식에 없음


def test_legacy_cell_with_wrong_label_is_not_trusted(tmp_path):
    # 사업번호 줄과 사업명 줄이 뒤바뀐 양식: 예전 좌표 칸은 있지만 라벨이 다르다 → 라벨을 따른다.
    swap = {(1, 0): "사업명", (2, 0): "사업번호"}
    cells = [(r, c, rs, cs, swap.get((r, c), t)) for r, c, rs, cs, t in standard_cells()]
    tpl = write_hwpx(tmp_path / "t.hwpx", cells)
    layout = analyze_template(str(tpl))
    assert layout.scalar["사업번호"] == (2, 3) and layout.scalar["사업명"] == (1, 3)
    out = tmp_path / "o.hwpx"
    fill_document(str(tpl), [PAGE], str(out))
    cells_out = read_cells(out)[0]
    assert cells_out[(2, 3)] == "1-1~2" and cells_out[(1, 3)] == "사물함"


def test_row_count_follows_template(tmp_path):
    layout = _layout(tmp_path, standard_cells(data_rows=10))
    assert layout.rows_per_page == 10
    rows = [
        LedgerRow(date(2026, 3, 10), 1, i, f"사람{i}", "입금", 4000.0, 0.0, False, "사업수입")
        for i in range(1, 13)
    ]
    assert [len(p.rows) for p in split_pages(rows, layout.rows_per_page)] == [10, 2]


def test_other_label_names(tmp_path):
    cells = standard_cells(shift=1, labels={"총액": "합계", "날짜": "일자", "세목": "품목", "수입지출": "수입·지출"})
    layout = _layout(tmp_path, cells)
    assert layout.scalar["총액"] == (27, 5)
    assert layout.scalar["날짜"] == (4, 3)
    assert layout.scalar["수입지출"] == (4, 8)
    assert layout.semok_col == 0 and layout.data_rows[0] == 14


def test_unknown_labels_on_legacy_positions_keep_working_with_notice(tmp_path):
    # 라벨 이름이 달라 확인은 못 하지만 예전 좌표 칸은 그대로 있는 양식 → 예전처럼 채우고 알린다.
    layout = _layout(tmp_path, standard_cells(labels={"사업번호": "번호", "세목": "적요"}))
    assert layout.scalar == LEGACY_SCALAR and layout.rows_per_page == 13
    assert any("'사업번호'" in n for n in layout.notes)
    assert any("세목·수량·단가" in n for n in layout.notes)


def test_missing_fields_are_reported(tmp_path):
    cells = [c for c in standard_cells(shift=1) if c[4] != "비 고" and c[:2] != (28, 4)]
    with pytest.raises(TemplateError) as exc:
        _layout(tmp_path, cells)
    assert "'비고'" in str(exc.value)


def test_not_a_template(tmp_path):
    bad = tmp_path / "x.hwpx"
    bad.write_bytes(b"PK\x03\x04 not really")
    with pytest.raises(TemplateError):
        analyze_template(str(bad))


def test_cell_without_empty_run_is_filled(tmp_path):
    tpl = tmp_path / "t.hwpx"
    data = build_hwpx(standard_cells()).replace(b"", b"")
    import io
    import zipfile

    z = zipfile.ZipFile(io.BytesIO(data))
    xml = z.read("Contents/section0.xml").decode()
    # 사업번호 값 칸만 빈 run 없이 문단만 있는 모양으로 바꾼다
    xml = re.sub(
        r'(<hp:tc><hp:subList><hp:p [^>]*>)<hp:run charPrIDRef="1"/>(</hp:p></hp:subList><hp:cellAddr colAddr="3" rowAddr="1"/>)',
        r"\1\2", xml, count=1,
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as out:
        out.writestr("mimetype", "application/hwp+zip", compress_type=zipfile.ZIP_STORED)
        out.writestr("Contents/section0.xml", xml)
    tpl.write_bytes(buf.getvalue())
    out_path = tmp_path / "o.hwpx"
    fill_document(str(tpl), [PAGE], str(out_path))
    assert read_cells(out_path)[0][(1, 3)] == "1-1~2"


def test_prefilled_value_cell_is_reported_not_overwritten(tmp_path):
    cells = [(r, c, rs, cs, "예: 1-1" if (r, c) == (1, 3) else t) for r, c, rs, cs, t in standard_cells()]
    tpl = write_hwpx(tmp_path / "t.hwpx", cells)
    problems: list[str] = []
    out = tmp_path / "o.hwpx"
    fill_document(str(tpl), [PAGE], str(out), problems=problems)
    assert read_cells(out)[0][(1, 3)] == "예: 1-1"
    assert problems and "사업번호" in problems[0]


def test_too_many_rows_for_template_is_error(tmp_path):
    tpl = write_hwpx(tmp_path / "t.hwpx", standard_cells(data_rows=1))
    with pytest.raises(ValueError):
        fill_document(str(tpl), [PAGE], str(tmp_path / "o.hwpx"))


def test_author_labels_with_offset_merges(tmp_path):
    # 26-1 학복위 양식: 직위·성명 라벨과 오른쪽 칸이 한 줄씩 어긋나게 병합돼 있다.
    from src.fill_hwpx import SECTION, find_label_targets, table_paragraph
    import zipfile
    cells = [c for c in standard_cells() if not (5 <= c[0] <= 11)]
    cells += [
        (5, 0, 1, 7, "책임자"), (5, 7, 1, 5, "증빙서류"),
        (6, 0, 1, 1, "소속"), (6, 1, 1, 6, ""), (6, 7, 6, 5, ""),
        (7, 0, 2, 1, "직위"), (7, 1, 1, 6, ""),
        (8, 1, 2, 6, ""),
        (9, 0, 2, 1, "성명"),
        (10, 1, 2, 6, ""),
        (11, 0, 1, 1, "연락처"),
    ]
    tpl = write_hwpx(tmp_path / "t.hwpx", cells)
    xml = zipfile.ZipFile(tpl).read(SECTION).decode()
    targets = find_label_targets(table_paragraph(xml), layout=analyze_template(str(tpl)))
    assert targets == {"소속": (6, 1), "직위": (7, 1), "성명": (8, 1), "연락처": (10, 1)}


def _evi_img():
    from io import BytesIO
    from PIL import Image
    from src.evidence_image import load_evidence_image
    buf = BytesIO()
    Image.new("RGB", (200, 100), "white").save(buf, "PNG")
    return load_evidence_image(buf.getvalue())


def test_evidence_picture_goes_below_label_in_26_1_layout(tmp_path):
    # 26-1 양식: '증빙서류' 라벨(5행 7열) 아래 큰 칸(6행 7열)에 그림
    import zipfile
    from src.fill_hwpx import SECTION, find_evidence_target, table_paragraph
    cells = [c for c in standard_cells() if not (5 <= c[0] <= 11)]
    cells += [(5, 0, 1, 7, "책임자"), (5, 7, 1, 5, "증빙서류"), (6, 0, 1, 1, "소속"), (6, 1, 1, 6, ""),
              (6, 7, 6, 5, ""), (7, 0, 5, 7, "")]
    tpl = write_hwpx(tmp_path / "t.hwpx", cells)
    layout = analyze_template(str(tpl))
    assert find_evidence_target(table_paragraph(zipfile.ZipFile(tpl).read(SECTION).decode()), layout) == (6, 7)
    out = tmp_path / "o.hwpx"
    pages = [{**PAGE, "증빙이미지": True}, {**PAGE, "증빙이미지": False}]
    fill_document(str(tpl), pages, str(out), evidence_image=_evi_img())
    z = zipfile.ZipFile(out)
    x = z.read(SECTION).decode()
    assert x.count("<hp:pic") == 1 and 'binaryItemIDRef="EVI0001"' in x
    assert "BinData/EVI0001.png" in z.namelist()


def test_evidence_picture_right_of_label_in_standard_layout(tmp_path):
    import zipfile
    from src.fill_hwpx import SECTION, find_evidence_target, table_paragraph
    tpl = write_hwpx(tmp_path / "t.hwpx", standard_cells())
    xml = table_paragraph(zipfile.ZipFile(tpl).read(SECTION).decode())
    assert find_evidence_target(xml, analyze_template(str(tpl))) == (6, 7)


def test_no_picture_without_flag(tmp_path):
    import zipfile
    tpl = write_hwpx(tmp_path / "t.hwpx", standard_cells())
    out = tmp_path / "o.hwpx"
    fill_document(str(tpl), [PAGE], str(out), evidence_image=_evi_img())
    z = zipfile.ZipFile(out)
    assert "<hp:pic" not in z.read("Contents/section0.xml").decode()
    assert not any(n.startswith("BinData/") for n in z.namelist())
