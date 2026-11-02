"""4단계(미리보기·수정·이름 제안) 테스트."""
from __future__ import annotations

import html
import io
import re
import zipfile
from datetime import date

import openpyxl
import pytest

from hwpx_builder import build_hwpx, read_cells, standard_cells
from src.config_gen import parse_overdue_rules_data
from src.parse_ledger import LedgerRow, Page
from src.remarks import OverdueRecord, RefundRecord, build_page_remark, similar_names
from web.semester_inputs import build_semester_from_overview

RULES = parse_overdue_rules_data({"default_per_day": 2000, "categories": []})


def _row(name, income=0, expense=0, overview="", no=2):
    return LedgerRow(date(2026, 3, 23), no, 1, name, overview, income, expense, False, "x")


def _page(*rows, kind="수입"):
    return Page(date(2026, 3, 23), kind, "x", list(rows))


# ── 이름 매칭 ────────────────────────────────────────────

def test_spacing_only_difference_matches_with_notice():
    sem = build_semester_from_overview("1. 사물함\n2. 물품 대여", RULES)
    overdue = [OverdueRecord(None, "우산 1개", "김 철수", 2000, None)]
    res = build_page_remark(2, _page(_row("김철수", 2000)), [], [], sem)  # 목록 비어 있음
    assert res.text == ""
    res = build_page_remark(2, _page(_row("김철수", 2000)), overdue, [], sem)
    assert res.text == "김 철수님 우산 1개 대여 후 1일 연체되어 연체료 2,000원 부과"
    assert res.review_items[0].advisory and "띄어쓰기" in res.review_items[0].reason


def test_unmatched_name_suggests_similar():
    sem = build_semester_from_overview("1. 사물함\n2. 물품 대여", RULES)
    overdue = [OverdueRecord(None, "우산 1개", "김철쑤", 2000, None), OverdueRecord(None, "우산", "박영희", 2000, None)]
    res = build_page_remark(2, _page(_row("김철수", 2000)), overdue, [], sem)
    assert res.text == ""
    assert not res.review_items[0].advisory
    assert "비슷한 이름: 김철쑤" in res.review_items[0].reason
    assert "박영희" not in res.review_items[0].reason


def test_refund_spacing_and_suggestion():
    sem = build_semester_from_overview("1. 사물함\n2. 물품 대여", RULES)
    refund = [RefundRecord("1", "이 도윤", 4000, "두번송금"), RefundRecord("2", "정예린", 2000, "두번송금")]
    page = _page(_row("이도윤", expense=4000, overview="환불", no=1), _row("정애린", expense=2000, overview="환불", no=1), kind="지출")
    res = build_page_remark(1, page, [], refund, sem)
    assert "이도윤님은 두 번 송금으로 인하여 환불 진행" in res.text
    reasons = [r.reason for r in res.review_items]
    assert any("띄어쓰기" in r for r in reasons)
    assert any("'정애린' 매칭 실패 — 비슷한 이름: 정예린" in r for r in reasons)


def test_similar_names_ignores_unrelated():
    assert similar_names("김철수", ["홍길동", "이영희"]) == []


# ── 웹 미리보기 흐름 ───────────────────────────────────────

def _xlsx(rows):
    buf = io.BytesIO()
    wb = openpyxl.Workbook()
    for r in rows:
        wb.active.append(r)
    wb.save(buf)
    return buf.getvalue()


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient

    from web.app import app

    return TestClient(app)


def _generate(client, cells=None):
    files = {
        "template": ("t.hwpx", build_hwpx(cells or standard_cells())),
        "ledger": ("l.xlsx", _xlsx([
            ["날짜", "사업번호", "세부번호", "기재내용", "구분", "개요", "수입", "지출"],
            ["2026-03-10", 1, 1, "전자01김하늘", "사업수입", "사물함 입금", 4000, 0],
            ["2026-03-23", 2, 1, "김철수", "사업수입", "연체료 입금", 2000, 0],
        ])),
        "overdue": ("o.xlsx", _xlsx([["반납일", "대여물품", "성명", "연체료", "비고"], ["2026-03-23", "우산 1개", "김철쑤", 2000, None]])),
        "refund": ("r.xlsx", _xlsx([["이름", "사유"]])),
    }
    data = {"business_overview": "1. 사물함 배부 사업\n2. 물품 대여 사업", "overdue_rules_text": "1DAY ₩2,000: 그 외 물품"}
    r = client.post("/generate", files=files, data=data, headers={"Accept": "text/html"})
    assert r.status_code == 200, r.text[:800]
    return r, r.text.split('action="/build/')[1].split('"')[0]


def _zip(client, result_html):
    job = result_html.split("/download/")[1].split('"')[0]
    return zipfile.ZipFile(io.BytesIO(client.get(f"/download/{job}").content))


def test_preview_shows_pages_and_suggestions(client):
    r, pid = _generate(client)
    assert "미리보기" in r.text and "이대로 문서 만들기" in r.text
    assert 'name="e-1-1-remark"' in r.text and 'name="e-2-1-class"' in r.text
    assert "비슷한 이름: 김철쑤" in r.text
    assert "전자 사물함은 대여비 4,000원" in r.text


def test_edits_are_applied_logged_and_kept(client):
    _, pid = _generate(client)
    edits = {
        "e-2-1-remark": "김철수님 우산 1개 대여 후 1일 연체되어 연체료 2,000원 부과\n(이름 오타: 김철쑤)",
        "e-2-1-class": "사업수입",          # 원래 값과 같음 → 수정 아님
        "e-9-9-remark": "없는 페이지",       # 무시
        "e-1-1-content": "  사물함 대여비 입금  ",
    }
    r = client.post(f"/build/{pid}", data=edits)
    assert r.status_code == 200
    assert "고친 칸 2개" in r.text or "2개가 반영" in r.text
    z = _zip(client, r.text)
    doc2 = [n for n in z.namelist() if n.endswith(".hwpx") and "_2_" in n][0]
    cells = read_cells(z.read(doc2))[0]
    assert cells[(27, 4)] == "김철수님 우산 1개 대여 후 1일 연체되어 연체료 2,000원 부과/(이름 오타: 김철쑤)"
    doc1 = [n for n in z.namelist() if n.endswith(".hwpx") and "_1_" in n][0]
    assert read_cells(z.read(doc1))[0][(4, 3)] == "사물함 대여비 입금"
    review = z.read("검수목록.txt").decode()
    assert "[입력확인-권장] 직접수정: 사업2 / 1페이지 '비고'" in review
    assert "사업1 / 1페이지 '내용'" in review

    # 미리보기로 돌아가면 고친 값이 남아 있고, 다시 원래대로 만들 수도 있다
    back = client.get(f"/preview/{pid}").text
    assert "(이름 오타: 김철쑤)" in back
    r2 = client.post(f"/build/{pid}", data={})
    z2 = _zip(client, r2.text)
    assert "직접수정" not in z2.read("검수목록.txt").decode()


def test_unknown_preview_is_404(client):
    assert client.get("/preview/없음").status_code == 404
    assert client.post("/build/없음", data={}).status_code == 404


def test_other_template_flows_through_preview(client):
    r, pid = _generate(client, standard_cells(shift=1, data_rows=8, labels={"총액": "합계"}))
    assert "한 페이지 세목 8줄" in r.text
    assert "다른 양식이라 라벨로 칸을 찾았습니다" in r.text
    z = _zip(client, client.post(f"/build/{pid}", data={}).text)
    doc1 = [n for n in z.namelist() if n.endswith(".hwpx") and "_1_" in n][0]
    cells = read_cells(z.read(doc1))[0]
    assert cells[(2, 3)] == "1-1" and cells[(14, 0)] == "전자01김하늘" and cells[(22, 5)] == "4,000"


def test_bad_template_is_friendly_400(client):
    cells = [c for c in standard_cells() if "비 고" != c[4] and c[:2] != (27, 4)]
    files = {
        "template": ("t.hwpx", build_hwpx(cells)),
        "ledger": ("l.xlsx", _xlsx([["날짜", "사업번호", "기재내용", "수입", "지출"], ["2026-03-10", 1, "김", 4000, 0]])),
        "overdue": ("o.xlsx", _xlsx([["대여물품", "성명", "연체료"]])),
        "refund": ("r.xlsx", _xlsx([["이름", "사유"]])),
    }
    r = client.post("/generate", files=files, data={"business_overview": "1. 사업", "overdue_rules_text": "1DAY ₩2,000: 그 외 물품"},
                    headers={"Accept": "text/html"})
    assert r.status_code == 400
    text = html.unescape(r.text)
    assert "'비고' 칸을 찾지 못했습니다" in text
    assert "위치:" not in text
