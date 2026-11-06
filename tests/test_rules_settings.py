"""2단계(비고 규칙 설정화·설정 파일) 테스트 — `python -m pytest tests`."""
from __future__ import annotations

import io
import json
import zipfile
from datetime import date
from pathlib import Path

import openpyxl
import pytest

from src.config_gen import parse_overdue_rules_data
from src.parse_ledger import LedgerRow, Page
from src.remarks import OverdueRecord, RefundRecord, build_page_remark
from src.rules_config import DEFAULT_RULES, RulesError, build_rules
from src.semester_config import parse_semester_config_data
from web.semester_inputs import build_semester_from_overview, parse_business_overview
from web.settings_file import build_settings, parse_rules_text

from hwpx_builder import build_hwpx, standard_cells

OVERDUE_RULES = parse_overdue_rules_data({
    "default_per_day": 2000,
    "categories": [{"per_day": 1000, "keywords": ["보조배터리"]}, {"per_day": 3000, "keywords": ["폴라로이드"]}],
})


def _semester(overview="1. 사물함 배부 사업\n2. 물품 대여 사업", rules=None):
    return build_semester_from_overview(overview, OVERDUE_RULES, None, rules)


def _row(name, income=0, expense=0, overview="", business_no=2):
    return LedgerRow(
        date=date(2026, 3, 23), business_no=business_no, detail_no=1, item_text=name,
        overview=overview, income=income, expense=expense, has_error=False, classification="x",
    )


def _page(*rows, kind="수입"):
    return Page(date=date(2026, 3, 23), income_expense=kind, classification="x", rows=list(rows))


def _remark(page, semester, overdue=(), refund=(), business_no=2):
    return build_page_remark(business_no, page, list(overdue), list(refund), semester)


# ── 기본 규칙 = 예전 출력 ───────────────────────────────────

def test_default_rules_reproduce_previous_sentences():
    sem = _semester()
    overdue = [
        OverdueRecord(date(2026, 3, 23), "보조배터리 1개", "정하윤", 3000, None),
        OverdueRecord(date(2026, 3, 23), "폴라로이드 필름", "오민재", 3000, "필름값"),
        OverdueRecord(date(2026, 3, 23), "폴라로이드 1개", "강도현", 6000, "백서준'으로 입금"),
    ]
    page = _page(_row("정하윤", 3000), _row("오민재", 3000), _row("백서준", 6000))
    assert _remark(page, sem, overdue).text.split("\n") == [
        "정하윤님 보조배터리 1개 대여 후 3일 연체되어 연체료 3,000원 부과",
        "오민재님 폴라로이드 필름 3장 추가로 소모하여 3,000원 부과",
        "강도현(‘백서준’으로 입금)님 폴라로이드 1개 대여 후 2일 연체되어 연체료 6,000원 부과",
    ]


def test_default_refund_lines_and_grouping():
    sem = _semester()
    refund = [
        RefundRecord("1", "가", 4000, "당첨등록포기"),
        RefundRecord("2", "나", 4000, "당첨등록포기"),
        RefundRecord("3", "다", 4000, "두번송금"),
    ]
    page = _page(_row("가", expense=4000, overview="사물함 환불", business_no=1),
                 _row("나", expense=4000, overview="사물함 환불", business_no=1),
                 _row("다", expense=4000, overview="사물함 환불", business_no=1), kind="지출")
    assert _remark(page, sem, refund=refund, business_no=1).text.split("\n")[1:] == [
        "가님, 나님은 당첨",
        "등록 포기로 인하여 환불 진행",
        "다님은 두 번 송금으로 인하여 환불 진행",
    ]


# ── 바꾼 규칙 ──────────────────────────────────────────────

def test_custom_template_and_refund_keyword():
    rules = build_rules({
        "비고문장": {"연체": "{대상}이 {물품}을 {일수}일 늦게 반납 → 연체료 {금액}원"},
        "환불페이지_키워드": ["환불", "반환"],
        "환불사유_표현": {"두번송금": "중복 송금"},
    })
    sem = _semester(rules=rules)
    overdue = [OverdueRecord(date(2026, 3, 23), "보조배터리 1개", "정하윤", 3000, None)]
    assert _remark(_page(_row("정하윤", 3000)), sem, overdue).text == \
        "정하윤님이 보조배터리 1개을 3일 늦게 반납 → 연체료 3,000원"

    refund = [RefundRecord("1", "다", 4000, "두번송금")]
    page = _page(_row("다", expense=4000, overview="대여비 반환", business_no=1), kind="지출")
    assert _remark(page, sem, refund=refund, business_no=1).text.split("\n")[1] == \
        "다님은 중복 송금으로 인하여 환불 진행"


def test_note_kind_synonyms_and_spacing():
    rules = build_rules({"연체료비고_분류": {"손망실비": ["손망실비", "분실"]}})
    sem = _semester(rules=rules)
    overdue = [
        OverdueRecord(None, "폴라로이드 필름", "오민재", 2000, "필름 값"),  # 띄어쓰기 달라도 인식
        OverdueRecord(None, "우산 1개", "한지우", 15000, "분실"),
    ]
    text = _remark(_page(_row("오민재", 2000), _row("한지우", 15000)), sem, overdue).text
    assert text.split("\n") == [
        "오민재님 폴라로이드 필름 2장 추가로 소모하여 2,000원 부과",
        "한지우님 우산 1개 손망실비 15,000원 부과",
    ]


@pytest.mark.parametrize("note", ["백서준으로 입금", "백서준’으로 입금", "백서준 '으로 입금", "백서준 충당"])
def test_daenap_without_exact_apostrophe(note):
    sem = _semester()
    overdue = [OverdueRecord(None, "폴라로이드 1개", "강도현", 6000, note)]
    text = _remark(_page(_row("백서준", 6000)), sem, overdue).text
    assert text.startswith("강도현(‘백서준’으로 입금)님 폴라로이드 1개")


def test_daenap_does_not_match_inside_longer_name():
    sem = _semester()
    overdue = [OverdueRecord(None, "우산 1개", "강도현", 2000, "김백서준'으로 입금")]
    result = _remark(_page(_row("백서준", 2000)), sem, overdue)
    assert result.text == "" and result.review_items


def test_role_keywords_and_tags():
    rules = build_rules({"사업종류_키워드": {"대여": ["대여", "빌려"]}})
    names, roles = parse_business_overview("1. 우산 빌려주기\n2. 좌석 관리 [사물함]\n3. 축제 [일반]")
    assert names == {"1": "우산 빌려주기", "2": "좌석 관리", "3": "축제"}
    sem = build_semester_from_overview("1. 우산 빌려주기\n2. 좌석 관리 [사물함]\n3. 축제 대여 [일반]", None, None, rules)
    assert {k: v.role for k, v in sem.businesses.items()} == {1: "rental", 2: "locker", 3: "general"}


# ── 잘못된 규칙은 친절하게 거절 ──────────────────────────────

@pytest.mark.parametrize(
    "user, needle",
    [
        ({"비고문장": {"연체": "{대상} {이름} 연체"}}, "{이름}"),
        ({"비고문장": {"연체": "{대상 연체"}}, "중괄호"),
        ({"비고문장": {"없는틀": "x"}}, "없는틀"),
        ({"모르는항목": 1}, "모르는항목"),
        ({"사업종류_키워드": {"행사": ["축제"]}}, "행사"),
        ({"환불사유_묶음": {"당첨 등록 포기": ["하나만"]}}, "두 개"),
        ({"연체료비고_분류": {"분실": ["분실"]}}, "분실"),
        ({"환불페이지_키워드": []}, "하나 이상"),
    ],
)
def test_bad_rules_explain(user, needle):
    with pytest.raises(RulesError) as exc:
        build_rules(user)
    assert needle in str(exc.value)


def test_rules_text_json_error_points_to_line():
    with pytest.raises(RulesError) as exc:
        parse_rules_text('{\n  "환불페이지_키워드": ["환불",]\n}')
    assert "2번째 줄" in str(exc.value)


def test_rules_text_accepts_whole_settings_file_and_blank():
    assert parse_rules_text("").templates == build_rules().templates
    whole = json.dumps({"설정버전": 1, "비고규칙": {"환불페이지_키워드": ["반환"]}}, ensure_ascii=False)
    assert parse_rules_text(whole).refund_page_keywords == ("반환",)


# ── 설정 파일 ─────────────────────────────────────────────

def test_settings_file_round_trip_and_no_contact():
    rules = build_rules({"환불페이지_키워드": ["환불", "반환"]})
    settings = build_settings(
        semester_label="2026-2학기",
        author={"성명": "홍길동", "연락처": "010-0000-0000"},
        locker_deposit_remark="문구",
        film_fee_per_sheet=1000,
        business_overview="1. 사물함",
        overdue_rules_text="1DAY ₩1,000: 우산",
        rules=rules,
    )
    text = json.dumps(settings, ensure_ascii=False)
    assert "010-" not in text
    assert parse_rules_text(text).refund_page_keywords == ("환불", "반환")
    assert settings["기본정보"]["학기명"] == "2026-2학기"


def test_cli_config_reads_rules_and_roles():
    cfg = parse_semester_config_data({
        "label": "2026-2학기",
        "businesses": {"1": {"name": "좌석 관리", "role": "사물함"}, "2": {"name": "우산 물품 대여"}},
        "overdue_rules": {"default_per_day": 2000, "categories": []},
        "locker": {"refund_fee_advisory": "수수료 확인"},
        "비고규칙": {"환불페이지_키워드": ["반환"]},
    })
    assert cfg.businesses[1].role == "locker" and cfg.businesses[2].role == "rental"
    assert cfg.rules.refund_page_keywords == ("반환",)
    assert cfg.rules.refund_fee_advisory == "수수료 확인"


def test_default_rules_json_is_valid():
    assert build_rules(json.loads(json.dumps(DEFAULT_RULES))).templates == build_rules().templates


# ── 웹: 설정.json이 zip에 들어가고 규칙 오류는 400 ─────────────

def _xlsx(rows):
    buf = io.BytesIO()
    wb = openpyxl.Workbook()
    for r in rows:
        wb.active.append(r)
    wb.save(buf)
    return buf.getvalue()


def _mini_template() -> bytes:
    return build_hwpx(standard_cells())


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient

    from web.app import app

    return TestClient(app)


def _post(client, rules_json=""):
    files = {
        "template": ("t.hwpx", _mini_template()),
        "ledger": ("l.xlsx", _xlsx([
            ["날짜", "사업번호", "세부번호", "기재내용", "구분", "개요", "수입", "지출"],
            ["2026-03-12", 1, 1, "다", "환급", "대여비 반환", 0, 4000],
        ])),
        "overdue": ("o.xlsx", _xlsx([["반납일", "대여물품", "성명", "연체료", "비고"]])),
        "refund": ("r.xlsx", _xlsx([["이름", "사유"], ["다", "두번송금"]])),
    }
    data = {
        "business_overview": "1. 좌석 관리 [사물함]",
        "overdue_rules_text": "1DAY ₩2,000: 그 외 물품",
        "semester_label": "2026-2학기",
        "author_contact": "010-0000-0000",
        "rules_json": rules_json,
    }
    return client.post("/generate", files=files, data=data, headers={"Accept": "text/html"})


def test_web_settings_json_in_zip(client):
    rules = json.dumps({"환불페이지_키워드": ["반환"]}, ensure_ascii=False)
    r = _post(client, rules)
    assert r.status_code == 200, r.text[:500]
    preview_id = r.text.split('action="/build/')[1].split('"')[0]
    r = client.post(f"/build/{preview_id}", data={})
    assert r.status_code == 200, r.text[:500]
    job = r.text.split("/download/")[1].split('"')[0]
    z = zipfile.ZipFile(io.BytesIO(client.get(f"/download/{job}").content))
    settings = json.loads(z.read("설정.json"))
    assert settings["사업개요"] == "1. 좌석 관리 [사물함]"
    assert settings["비고규칙"]["환불페이지_키워드"] == ["반환"]
    assert "010-" not in z.read("설정.json").decode()
    review = z.read("검수목록.txt").decode()
    assert "수수료" in review  # '반환' 페이지를 환불 페이지로 인식 → 수수료 검수권장


def test_web_bad_rules_is_400(client):
    r = _post(client, '{"비고문장": {"연체": "{없는칸}"}}')
    assert r.status_code == 400
    assert "{없는칸}" in r.text


def test_retired_rule_key_is_ignored():
    # 잠깐 배포됐던 '구분_표기'가 든 예전 설정 파일도 그대로 불러올 수 있어야 한다.
    from src.rules_config import build_rules
    assert build_rules({"구분_표기": {"수입의환급": "환불"}}).source == build_rules().source
