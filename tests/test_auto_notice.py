"""26-1 최종본 비교로 넣은 규칙과 [자동처리] 알림 테스트."""
from __future__ import annotations

from datetime import date

from src.config_gen import lookup_per_day, parse_overdue_rules_data
from src.parse_ledger import LedgerRow, parse_ledger_workbook, split_pages
from src.pipeline import ReviewItem, format_review_report, run_engine, split_refund_fee
from src.remarks import OverdueRecord, RefundRecord
from src.sheet_reader import InputIssue
from web.semester_inputs import build_semester_from_overview

RULES = parse_overdue_rules_data({
    "default_per_day": 2000,
    "categories": [
        {"per_day": 3000, "keywords": ["돗자리", "폴라로이드"]},
        {"per_day": 2000, "keywords": ["돗자리(소)"]},
        {"per_day": 1000, "keywords": ["보조배터리"]},
    ],
})


def _row(no, detail, name, income=0, expense=0, overview="개요", d=date(2026, 3, 16), cls="x"):
    return LedgerRow(d, no, detail, name, overview, income, expense, False, cls)


def test_more_specific_keyword_wins():
    assert lookup_per_day("돗자리(소) 1개", RULES) == 2000
    assert lookup_per_day("돗자리(대) 1개", RULES) == 3000
    assert lookup_per_day("돗자리 1개", RULES) == 3000


# ── 쪽 나누기 ─────────────────────────────────────────────

def test_split_on_vendor_for_general_only():
    rows = [_row(4, 2, "글터문구센터", expense=45000), _row(4, 3, "시립대학구내서적", expense=500)]
    pages = split_pages(rows, 13, split_on_item=True)
    assert [len(p.rows) for p in pages] == [1, 1] and pages[1].split_reason == "거래처"
    assert [len(p.rows) for p in split_pages(rows, 13, split_on_item=False)] == [2]


def test_same_vendor_stays_together():
    rows = [_row(8, 4, "씨유", expense=2200), _row(8, 5, "씨유", expense=4400)]
    assert [len(p.rows) for p in split_pages(rows, 13, split_on_item=True)] == [2]


def test_split_on_overview_change():
    rows = [_row(8, 1, "박", expense=1, overview="수박 구매"), _row(8, 2, "박", expense=1, overview="물품 구매")]
    pages = split_pages(rows, 13)
    assert [len(p.rows) for p in pages] == [1, 1] and pages[1].split_reason == "개요"


def test_locker_pages_not_split_by_name():
    sem = build_semester_from_overview("1. 사물함 배부 사업\n4. 봄 축제", RULES)
    rows = {1: [_row(1, i, f"전자{i:02d}사람", income=4000, cls="수익") for i in range(1, 4)],
            4: [_row(4, 1, "가게A", expense=100), _row(4, 2, "가게B", expense=200)]}
    res = run_engine(rows, sem, [], [])
    assert len(res.businesses[1].pages) == 1
    assert len(res.businesses[4].pages) == 2
    autos = [r for r in res.review_items if r.auto and r.business_no == 4]
    assert autos and "거래처" in autos[0].reason and autos[0].format_line().startswith("[자동처리] (사업4/2페이지)")


# ── 환불 수수료 분리 ────────────────────────────────────────

REFUND = [RefundRecord("1", "가", 4000, "두번송금"), RefundRecord("2", "나", 2000, "두번송금")]


def test_fee_split_when_consistent():
    rows = [_row(1, 1, "가", expense=4750), _row(1, 2, "나", expense=2750)]
    s = split_refund_fee(rows, REFUND, 13)
    assert s.done
    assert s.items == ["가", "나", "수수료"] and s.quantities == ["1", "1", "2"]
    assert s.unit_prices == ["4,000", "2,000", "750"]


def test_fee_not_split_when_differences_vary():
    rows = [_row(1, 1, "가", expense=4750), _row(1, 2, "나", expense=2500)]
    s = split_refund_fee(rows, REFUND, 13)
    assert not s.done and "일정하지 않아" in s.message


def test_fee_not_split_when_unmatched_or_no_room():
    assert not split_refund_fee([_row(1, 1, "없는사람", expense=4750)], REFUND, 13).done
    rows = [_row(1, 1, "가", expense=4750), _row(1, 2, "나", expense=2750)]
    s = split_refund_fee(rows, REFUND, 2)
    assert not s.done and "칸이 모자라" in s.message


def test_fee_split_in_engine_keeps_total_and_notes():
    sem = build_semester_from_overview("1. 사물함 배부 사업", RULES)
    rows = {1: [_row(1, 1, "가", expense=4750, overview="사물함 환불", cls="수입의환급"),
                _row(1, 2, "나", expense=2750, overview="사물함 환불", cls="수입의환급")]}
    res = run_engine(rows, sem, [], REFUND)
    page = res.businesses[1].pages[0]
    assert page.total_amount == "7,500"
    assert page.items[-1] == "수수료" and page.unit_prices[-1] == "750"
    assert any(r.auto and "수수료 2 × 750원" in r.reason for r in res.review_items)
    assert not any("합본금액" in r.reason for r in res.review_items)


# ── 그 밖의 알림 ──────────────────────────────────────────

def test_detail_gap_is_flagged():
    sem = build_semester_from_overview("1. 사물함 배부 사업", RULES)
    rows = {1: [_row(1, 1, "가", income=4000), _row(1, 3, "나", income=4000)]}
    res = run_engine(rows, sem, [], [])
    assert any("세부번호가 이어지지 않습니다(1, 3)" in r.reason and not r.advisory for r in res.review_items)


def test_rental_days_and_daenap_are_auto_noted():
    sem = build_semester_from_overview("3. 물품 대여 사업", RULES)
    overdue = [OverdueRecord(None, "돗자리(소) 1개", "대여자", 4000, "입금자'으로 입금")]
    res = run_engine({3: [_row(3, 1, "입금자", income=4000)]}, sem, overdue, [])
    page = res.businesses[3].pages[0]
    assert page.remark == "대여자(‘입금자’으로 입금)님 돗자리(소) 1개 대여 후 2일 연체되어 연체료 4,000원 부과"
    reasons = [r.reason for r in res.review_items if r.auto]
    assert any("대납으로 연결" in x for x in reasons)
    assert any("연체 2일 = 연체료 4,000원 ÷ 1일 단가 2,000원" in x for x in reasons)


def test_no_review_marker_inside_document_when_days_known():
    sem = build_semester_from_overview("3. 물품 대여 사업", RULES)
    overdue = [OverdueRecord(None, "우산 3개", "박", 6000, None)]
    res = run_engine({3: [_row(3, 1, "박", income=6000)]}, sem, overdue, [])
    assert res.businesses[3].pages[0].remark == "박님 우산 3개 대여 후 3일 연체되어 연체료 6,000원 부과"
    assert any("복수 수량" in r.reason and not r.advisory for r in res.review_items)


def test_summary_row_without_business_and_item_is_soft_notice():
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["날짜", "사업번호", "세부번호", "기재내용", "현금", "구분", "개요", "수입", "지출"])
    ws.append(["2026-03-10", 1, 1, "가", None, "x", "y", 4000, 0])
    ws.append(["#VALUE!", None, None, None, None, None, None, "10,481,993원", "5,454,540원"])
    issues: list[InputIssue] = []
    parse_ledger_workbook(wb, issues=issues)
    soft = [i for i in issues if "합계 줄" in i.message]
    assert soft and soft[0].advisory and not [i for i in issues if not i.advisory]


def test_report_groups_sections():
    items = [
        ReviewItem(1, 1, "가", "매칭 실패"),
        ReviewItem(1, 1, "", "연체 1일 계산", advisory=True, auto=True),
        ReviewItem(1, 2, "", "수수료 확인", advisory=True),
    ]
    text = format_review_report(items, [InputIssue("장부", "날짜 못 읽음", row=3)])
    order = [text.index(h) for h in ("■ 입력확인", "■ 검수필요", "■ 자동처리", "■ 참고")]
    assert order == sorted(order)
    assert "[자동처리] (사업1/1페이지) 연체 1일 계산" in text


def test_classification_spacing_is_removed_and_noted():
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["날짜", "사업번호", "세부번호", "기재내용", "구분", "개요", "수입", "지출"])
    ws.append([date(2026, 3, 10), 1, 1, "가", "지출의 환급", "y", 4000, 0])
    ws.append([date(2026, 3, 10), 1, 2, "나", "지출의환급", "y", 4000, 0])
    issues: list[InputIssue] = []
    grouped = parse_ledger_workbook(wb, issues=issues)
    assert [r.classification for r in grouped[1]] == ["지출의환급", "지출의환급"]
    assert len(split_pages(grouped[1])) == 1
    note = [i for i in issues if i.auto and "구분의 띄어쓰기" in i.message]
    assert note and "'지출의 환급'→'지출의환급' 1행" in note[0].message


def test_example_settings_follow_26_1_format():
    from web.settings_file import example_settings, parse_rules_text

    s = example_settings()
    assert s["기본정보"]["학기명"] == ""
    assert s["기본정보"]["작성자"] == {"성명": "", "직위": "학생복지위원장", "소속": "학생복지위원회"}
    assert "1. 1학기 온라인 사물함 배부 사업" in s["사업개요"] and "99. 기타" in s["사업개요"]
    assert s["연체료규정"].splitlines() == [
        "1DAY ₩1,000: 보조배터리",
        "1DAY ₩3,000: 폴라로이드, 앰프, 듀라테이블, 의자, 카트, 삼각대, 돗자리, 피크닉 테이블, 보드게임, 캐노피, 빔프로젝터",
        "1DAY ₩2,000: 그 외 물품",
    ]
    assert parse_rules_text("") .templates == parse_rules_text(__import__("json").dumps(s["비고규칙"])).templates


def test_ledger_business_missing_from_overview_is_reported(tmp_path):
    import io

    import openpyxl

    from hwpx_builder import build_hwpx, standard_cells
    from web.runner import prepare_run
    from web.semester_inputs import UploadConfig

    def xlsx(rows):
        buf = io.BytesIO(); wb = openpyxl.Workbook()
        for r in rows: wb.active.append(r)
        wb.save(buf); buf.seek(0); return buf

    tpl = tmp_path / "t.hwpx"; tpl.write_bytes(build_hwpx(standard_cells()))
    sem = build_semester_from_overview("1. 사물함 배부 사업\n5. 간식행사", RULES)
    prep = prepare_run(
        xlsx([["날짜", "사업번호", "기재내용", "구분", "개요", "수입", "지출"],
              ["2026-03-10", 1, "가", "x", "y", 4000, 0], ["2026-03-11", 7, "나", "x", "z", 0, 100]]),
        xlsx([["대여물품", "성명", "연체료"]]), xlsx([["이름", "사유"]]).getvalue(),
        upload=UploadConfig(semester=sem, business_overview="", overdue_rules_text="", template_hwpx_path=tpl),
        work_dir=tmp_path / "w",
    )
    lines = [i.format_line() for i in prep.input_issues]
    assert any("[입력확인] 사업개요: 장부에 사업번호 7번이 있는데 사업개요에 없어" in l for l in lines)
    assert any("5번" in l and "장부에 거래가 없어" in l for l in lines)


def test_first_page_has_26_1_defaults():
    from fastapi.testclient import TestClient

    from web.app import app

    html = TestClient(app).get("/").text
    assert 'value="학생복지위원회"' in html and 'value="학생복지위원장"' in html
    assert "1. 1학기 온라인 사물함 배부 사업" in html
    assert 'id="sem-year"' in html and 'name="semester_label"' in html
    assert "관리자용" in html


def test_keyword_matching_ignores_spacing():
    from web.semester_inputs import EXAMPLE_OVERDUE_RULES
    from src.parse_overdue_rules import parse_overdue_rules_text

    rules = parse_overdue_rules_text(EXAMPLE_OVERDUE_RULES)
    assert lookup_per_day("피크닉 테이블 1개", rules) == 3000
    assert lookup_per_day("피크닉테이블 1개", rules) == 3000
    assert lookup_per_day("보조배터리 1개", rules) == 1000
    assert lookup_per_day("삼각대 1개", rules) == 3000
    assert lookup_per_day("우산 1개", rules) == 2000


def test_refund_class_must_match_side():
    # 환불(돌려준 돈)은 지출·'수입의환급'. 수입 칸에 '수입의환급'이 있으면 검수필요로 알린다(고치지는 않음).
    sem = build_semester_from_overview("1. 사물함 배부 사업", RULES)
    rows = {1: [_row(1, 1, "가", income=4000, cls="지출의환급"),
                _row(1, 2, "나", expense=4000, overview="사물함 환불", cls="수입의환급", d=date(2026, 3, 17)),
                _row(1, 3, "다", expense=4000, overview="사물함 환불", cls="지출의환급", d=date(2026, 3, 18))]}
    res = run_engine(rows, sem, [], [])
    hits = [r for r in res.review_items if "구분이 '지출의환급'이면 수입" in r.reason]
    assert len(hits) == 1 and hits[0].page_index == 3 and not hits[0].advisory
    assert res.businesses[1].pages[2].classification == "지출의환급"


def test_renamed_hwp_gets_clear_message(tmp_path):
    # 확장자만 .hwpx로 바꾼 .hwp(OLE 형식) — 다른 이름으로 저장하라고 안내
    import pytest
    from web.semester_inputs import resolve_template
    with pytest.raises(ValueError) as exc:
        resolve_template(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 100, "양식.hwpx", tmp_path)
    assert "확장자만 바꾸면" in str(exc.value) and "다른 이름으로 저장" in str(exc.value)


def test_rental_income_pages_get_evidence_flag():
    sem = build_semester_from_overview("1. 사물함 배부 사업\n3. 물품 대여 사업\n4. 봄 축제", RULES)
    rows = {3: [_row(3, 1, "가", income=2000), _row(3, 2, "가게", expense=5000, d=date(2026, 3, 17))],
            1: [_row(1, 1, "나", income=4000)], 4: [_row(4, 1, "가게", expense=100)]}
    res = run_engine(rows, sem, [], [])
    assert [p.evidence_image for p in res.businesses[3].pages] == [True, False]
    assert not res.businesses[1].pages[0].evidence_image and not res.businesses[4].pages[0].evidence_image


def test_default_rental_evidence_image_exists():
    from src.evidence_image import default_rental_evidence
    img = default_rental_evidence()
    assert img is not None and img.ext == "jpg" and img.width_px > 0


def test_tripod_mat_boardgame_always_reviewed():
    # 삼각대·돗자리·보드게임: 규정 3,000원이지만 실제로 2,000원으로 받는 일이 많아 항상 검수필요.
    from web.semester_inputs import EXAMPLE_OVERDUE_RULES
    from src.parse_overdue_rules import parse_overdue_rules_text
    from src.remarks import _calc_overdue_days
    from src.rules_config import build_rules

    rules = parse_overdue_rules_text(EXAMPLE_OVERDUE_RULES)
    watch = build_rules().overdue_watch
    days, why = _calc_overdue_days(6000, "삼각대 1개", rules, watch)
    assert days == 2 and "실제로는 그 외 물품 금액" in why and "2,000원으로 받았다면 3일" in why
    days, why = _calc_overdue_days(2000, "돗자리(소) 1개", rules, watch)
    assert days is None and "받았다면 1일" in why
    days, why = _calc_overdue_days(3000, "앰프 1개", rules, watch)
    assert days == 1 and why is None
    assert build_rules({"연체료_항상검수_물품": []}).overdue_watch == ()


def test_default_overview_marks_library_locker_as_general():
    # '9. 중앙도서관 사물함 관리 운영'은 이름에 '사물함'이 있지만 대여비 사업이 아니라 일반 사업.
    from web.semester_inputs import EXAMPLE_OVERVIEW, build_semester_from_overview
    sem = build_semester_from_overview(EXAMPLE_OVERVIEW, RULES)
    assert sem.role_for(9) == "general" and sem.name_for(9) == "중앙도서관 사물함 관리 운영"
    assert sem.role_for(1) == "locker"


def test_hwp_template_gets_save_as_message(tmp_path):
    import pytest
    from web.semester_inputs import resolve_template
    with pytest.raises(ValueError) as exc:
        resolve_template(b"\xd0\xcf\x11\xe0" + b"\0" * 50, "양식.hwp", tmp_path)
    assert "HWPX 문서" in str(exc.value)
