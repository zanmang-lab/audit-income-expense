"""1단계(입력 유연화) 회귀 테스트 — `python -m pytest tests`."""
from __future__ import annotations

import io
import zipfile
from datetime import date, datetime

import openpyxl
import pytest

from src.fill_hwpx import fill_document, missing_label_fields
from src.parse_ledger import parse_ledger_workbook
from src.remarks import parse_overdue_sheet, parse_refund_sheet
from src.semester_config import parse_semester_config_data
from src.sheet_reader import ColumnError
from src.utils import format_date, parse_amount_value, parse_date_value
from web.masking import mask_refund_workbook
from web.semester_inputs import BasicInfo, build_semester_from_overview


def _wb(rows):
    wb = openpyxl.Workbook()
    ws = wb.active
    for r in rows:
        ws.append(r)
    return wb


def _xlsx_bytes(rows) -> bytes:
    buf = io.BytesIO()
    _wb(rows).save(buf)
    return buf.getvalue()


STANDARD_HEADER = ["날짜", "사업번호", "세부번호", "기재내용", "현금", "구분", "개요", "수입", "지출", "오류여부"]


# ── 값 읽기 ────────────────────────────────────────────────

@pytest.mark.parametrize(
    "value, expected",
    [
        (datetime(2026, 3, 5, 14, 0), date(2026, 3, 5)),
        ("2026-03-05", date(2026, 3, 5)),
        ("2026.3.5.", date(2026, 3, 5)),
        ("2026. 3. 5", date(2026, 3, 5)),
        ("2026/03/05 00:00:00", date(2026, 3, 5)),
        ("2026년 3월 5일", date(2026, 3, 5)),
        ("20260305", date(2026, 3, 5)),
        (46086, date(2026, 3, 5)),  # 엑셀 일련번호
        ("3/5", None),  # 연도 없음 → 추정하지 않음
        ("2026-02-30", None),
        ("", None),
        (None, None),
    ],
)
def test_parse_date_value(value, expected):
    assert parse_date_value(value) == expected


def test_format_date_accepts_text():
    assert format_date("2026.3.5") == "2026.03.05."


@pytest.mark.parametrize(
    "value, expected",
    [
        (4000, (4000, True)),
        (4000.0, (4000, True)),
        ("4,000", (4000, True)),
        ("4,000원", (4000, True)),
        ("₩ 4,000", (4000, True)),
        ("-4,000", (-4000, True)),
        ("(4,000)", (-4000, True)),
        ("", (None, True)),
        (None, (None, True)),
        ("사천원", (None, False)),
    ],
)
def test_parse_amount_value(value, expected):
    assert parse_amount_value(value) == expected


# ── 장부 ────────────────────────────────────────────────

def _flatten(grouped):
    return [
        (r.date, r.business_no, r.detail_no, r.item_text, r.classification, r.overview, r.income, r.expense)
        for no in sorted(grouped)
        for r in grouped[no]
    ]


def test_ledger_reads_by_header_regardless_of_order():
    standard = _wb([
        STANDARD_HEADER,
        [datetime(2026, 3, 10), 1, 1, "전자01김하늘", None, "사업수입", "사물함 입금", 4000, 0, None],
        [datetime(2026, 3, 12), 1, 2, "이도윤", None, "지출의환급", "사물함 환불", 0, 4000, None],
    ])
    # 열 순서를 섞고, 현금 열을 빼고, 메모 열을 끼우고, 날짜·금액을 글자로 적은 장부
    shuffled = _wb([
        ["메모", "수입", "지출", "사업번호", "날짜", "기재내용", "개요", "구분", "세부번호"],
        ["", "4,000", "", 1, "2026.3.10", "전자01김하늘", "사물함 입금", "사업수입", 1],
        ["x", "", "4,000원", "1", "2026-03-12", "이도윤", "사물함 환불", "지출의환급", 2],
    ])
    assert _flatten(parse_ledger_workbook(shuffled)) == _flatten(parse_ledger_workbook(standard))


def test_ledger_header_not_on_first_row():
    wb = _wb([
        ["2026-1학기 학복위 장부"],
        [],
        STANDARD_HEADER,
        ["2026-03-10", 1, 1, "김하늘", None, "사업수입", "입금", 4000, 0, None],
    ])
    grouped = parse_ledger_workbook(wb)
    assert grouped[1][0].item_text == "김하늘"


def test_ledger_reports_rows_it_cannot_use():
    wb = _wb([
        STANDARD_HEADER,
        ["2026-03-10", 1, 1, "정상", None, "사업수입", "입금", 4000, 0, None],
        ["3/10", 1, 2, "연도없는날짜", None, "사업수입", "입금", 4000, 0, None],
        ["2026-03-10", 1, 3, "음수", None, "사업수입", "입금", -4000, 0, None],
        ["2026-03-10", 1, 4, "둘다", None, "사업수입", "입금", 4000, 1000, None],
        ["2026-03-10", 1, 5, "글자금액", None, "사업수입", "입금", "사천원", 0, None],
        ["2026-03-10", "?", 6, "사업번호없음", None, "사업수입", "입금", 4000, 0, None],
        ["2026-03-10", 1, 7, "영원", None, "사업수입", "입금", 0, 0, None],
        [None, None, None, "합계", None, None, None, 8000, 0, None],  # 합계 줄은 조용히 넘김
    ])
    issues = []
    grouped = parse_ledger_workbook(wb, issues=issues)
    assert [r.item_text for r in grouped[1]] == ["정상"]

    required = [i for i in issues if not i.advisory]
    advisory = [i for i in issues if i.advisory]
    assert [i.row for i in required] == [3, 4, 5, 6, 7]
    assert "날짜를 읽지 못해" in required[0].message
    assert "음수" in required[1].message
    assert "함께" in required[2].message
    assert "숫자로 읽지 못해" in required[3].message
    assert "사업번호를 읽지 못해" in required[4].message
    assert [i.row for i in advisory] == [8]
    assert required[0].format_line().startswith("[입력확인] 장부 3행:")


def test_ledger_missing_required_column_explains():
    wb = _wb([["날짜", "사업번호", "기재내용", "수입"], ["2026-03-10", 1, "김", 4000]])
    with pytest.raises(ColumnError) as exc:
        parse_ledger_workbook(wb)
    assert "'지출'" in str(exc.value)
    assert "날짜, 사업번호, 기재내용, 수입" in str(exc.value)


def test_ledger_without_headers_falls_back_to_column_order():
    wb = _wb([
        ["a", "b", "c", "d", "e", "f", "g", "h", "i", "j"],
        [datetime(2026, 3, 10), 1, 1, "김하늘", None, "사업수입", "입금", 4000, 0, None],
    ])
    issues = []
    grouped = parse_ledger_workbook(wb, issues=issues)
    assert grouped[1][0].income == 4000
    assert any("기존 열 순서" in i.message for i in issues)


# ── 연체료·환불자 목록 ─────────────────────────────────────

def test_overdue_by_header():
    wb = _wb([
        ["성명", "비고", "연체료", "대여물품", "반납일(입금일)"],
        ["강도현", "백서준'으로 입금", "6,000", "폴라로이드 1개", "2026.3.23"],
    ])
    rec = parse_overdue_sheet(wb.active)[0]
    assert (rec.name, rec.fee, rec.goods, rec.note, rec.return_date) == (
        "강도현", 6000, "폴라로이드 1개", "백서준'으로 입금", date(2026, 3, 23))


def test_refund_name_header_synonym_and_reason_not_amount():
    wb = _wb([["사물함", "성명", "환불사유", "금액"], ["전자-1", "이도윤", "두번송금", 4000]])
    rec = parse_refund_sheet(wb.active)[0]
    assert (rec.name, rec.reason, rec.amount) == ("이도윤", "두번송금", 4000)


def test_masking_uses_header_synonyms_and_drops_sensitive():
    data = _xlsx_bytes([
        ["사물함 번호", "성명", "전화번호", "환불사유", "환불금액", "계좌번호"],
        ["전자-02", "이도윤", "010-0000-0001", "당첨등록포기", 4000, "000-0000-000001"],
        ["일반-05", "정예린", "010-0000-0002", "두번송금", 2000, "000-0000-000002"],
    ])
    masked, dropped = mask_refund_workbook(data)
    assert set(dropped) == {"전화번호", "계좌번호"}
    recs = parse_refund_sheet(openpyxl.load_workbook(io.BytesIO(masked)).active)
    assert [(r.name, r.reason, r.amount) for r in recs] == [
        ("이도윤", "당첨등록포기", 4000), ("정예린", "두번송금", 2000)]
    flat = [str(v) for row in openpyxl.load_workbook(io.BytesIO(masked)).active.iter_rows(values_only=True) for v in row]
    assert not any(v.startswith("010-") for v in flat)


def test_masking_fallback_never_copies_phone_column():
    # 헤더를 못 알아봐 열 순서로 읽을 때, 금액 자리(D열)에 전화번호가 있어도 옮기지 않는다.
    data = _xlsx_bytes([
        ["a", "b", "c", "d", "e"],
        ["전자-02", "이도윤", "", "010-0000-0001", "당첨등록포기"],
        ["일반-05", "정예린", "", "010-0000-0002", "두번송금"],
    ])
    masked, _ = mask_refund_workbook(data)
    flat = [str(v) for row in openpyxl.load_workbook(io.BytesIO(masked)).active.iter_rows(values_only=True) for v in row]
    assert not any(v.startswith("010-") for v in flat)


# ── 설정·기본 정보 ─────────────────────────────────────────

def test_semester_json_label_and_author_are_used():
    cfg = parse_semester_config_data({
        "label": "2026-1학기",
        "businesses": {"1": {"name": "사물함", "role": "locker"}},
        "overdue_rules": {"default_per_day": 2000, "categories": []},
        "author": {"성명": "홍길동", "직위": "총무", "모름": "x"},
    })
    assert cfg.label == "2026-1학기"
    assert cfg.author == {"성명": "홍길동", "직위": "총무"}


def test_basic_info_overrides_defaults():
    cfg = build_semester_from_overview(
        "1. 사물함 배부 사업",
        None,
        BasicInfo("2026-2학기", {"성명": "홍길동", "연락처": " "}, "전자 5,000원", "1,500"),
    )
    assert cfg.label == "2026-2학기_수입지출내역서"
    assert cfg.author == {"성명": "홍길동"}
    assert cfg.locker_deposit_remark == "전자 5,000원"
    assert cfg.film_fee_per_sheet == 1500


def test_basic_info_blank_keeps_defaults():
    plain = build_semester_from_overview("1. 사물함 배부 사업")
    blank = build_semester_from_overview("1. 사물함 배부 사업", None, BasicInfo())
    assert plain == blank


@pytest.mark.parametrize("bad", ["0", "abc", "-100"])
def test_basic_info_rejects_bad_film_fee(bad):
    with pytest.raises(ValueError):
        build_semester_from_overview("1. 대여 사업", None, BasicInfo(film_fee_per_sheet=bad))


# ── 양식: 작성자 칸을 라벨로 찾기 ───────────────────────────

def _cell(row, col, text="", colspan=1):
    run = f'<hp:run charPrIDRef="1"><hp:t>{text}</hp:t></hp:run>' if text else '<hp:run charPrIDRef="1"/>'
    return (
        f'<hp:tc><hp:subList><hp:p paraPrIDRef="0">{run}</hp:p></hp:subList>'
        f'<hp:cellAddr colAddr="{col}" rowAddr="{row}"/><hp:cellSpan colSpan="{colspan}" rowSpan="1"/></hp:tc>'
    )


def _mini_template(tmp_path, author_cells):
    cells = [_cell(1, 0, "사업번호", 3), _cell(1, 3, colspan=5)] + author_cells
    xml = (
        '<hs:sec><hp:p id="0" pageBreak="0"><hp:run><hp:tbl>'
        f'<hp:tr>{"".join(cells)}</hp:tr></hp:tbl></hp:run></hp:p></hs:sec>'
    )
    path = tmp_path / "tpl.hwpx"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("mimetype", "application/hwp+zip", compress_type=zipfile.ZIP_STORED)
        z.writestr("Contents/section0.xml", xml)
    return path


def _cells_text(path):
    import re
    x = zipfile.ZipFile(path).read("Contents/section0.xml").decode()
    out = {}
    for c in re.findall(r"<hp:tc>.*?</hp:tc>", x):
        m = re.search(r'colAddr="(\d+)" rowAddr="(\d+)"', c)
        out[(int(m.group(2)), int(m.group(1)))] = "".join(re.findall(r"<hp:t>([^<]*)</hp:t>", c))
    return out


def test_author_filled_next_to_labels(tmp_path):
    tpl = _mini_template(tmp_path, [
        _cell(5, 0, "성 명", 3), _cell(5, 3, colspan=2), _cell(5, 5, "직위"), _cell(5, 6, "예전총무", 2),
    ])
    out = tmp_path / "out.hwpx"
    fill_document(str(tpl), [{"사업번호": "1-1", "성명": "홍길동", "직위": "사무국 총무", "rows": []}], str(out))
    cells = _cells_text(out)
    assert cells[(1, 3)] == "1-1"
    assert cells[(5, 3)] == "홍길동"
    assert cells[(5, 6)] == "사무국 총무"  # 예전 값은 바뀜


def test_missing_author_labels_are_reported(tmp_path):
    tpl = _mini_template(tmp_path, [_cell(5, 0, "성명", 3), _cell(5, 3, colspan=2)])
    assert missing_label_fields(str(tpl), ["성명", "소속"]) == ["소속"]


def test_wrong_file_as_ledger_is_user_error():
    wb = _wb([["반납일(입금일)", "대여물품", "성명", "연체료", "비고"], ["2026-03-23", "우산 1개", "김", 2000, None]])
    with pytest.raises(ColumnError):
        parse_ledger_workbook(wb)
