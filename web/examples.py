"""첫 화면 ⑤~⑦의 예시 — 화면에 보이는 예시 표와 '예시 파일 받기' 엑셀.

이름은 모두 가상입니다(공개 화면이라 실제 학생 이름을 쓰지 않음).
"""
from __future__ import annotations

from html import escape
from pathlib import Path

import openpyxl


def _table(headers: list[str], rows: list[list[str]]) -> str:
    head = "".join(f"<th>{escape(h)}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{escape(c)}</td>" for c in r) + "</tr>" for r in rows)
    return f'<div class="example-scroll"><table class="example-table"><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'


def example_block(title: str, *, table_html: str, note: str, download_href: str, download_label: str) -> str:
    return (
        f'<details class="example"><summary>{escape(title)} 예시 보기</summary>'
        f"{table_html}"
        f'<p class="mt-2 muted">{note}</p>'
        f'<p class="mt-1"><a href="{download_href}" download>{escape(download_label)}</a></p>'
        "</details>"
    )


def upload_form_examples_html() -> dict[str, str]:
    return {
        "ledger": example_block(
            "장부",
            table_html=_table(
                ["날짜", "사업번호", "세부번호", "기재내용", "구분", "개요", "수입", "지출"],
                [
                    ["2026-03-10", "2", "1", "전자12홍길동", "지출의 환급", "대면 사물함 수익금", "4,000", "0"],
                    ["2026-03-23", "3", "1", "김하늘", "지출의 환급", "대여 물품 연체료 입금", "4,000", "0"],
                ],
            ),
            note=(
                "열은 머리글 이름으로 찾으니 순서나 다른 열(현금 등)은 상관없습니다. "
                "꼭 필요한 열은 날짜, 사업번호, 기재내용, 수입, 지출이고 세부번호, 구분, 개요, 오류여부는 있으면 씁니다. "
                "날짜는 2026-03-10, 2026.3.10, 2026년 3월 10일 모두 읽습니다. 시트가 여러 개면 DATA 시트를 고릅니다."
            ),
            download_href="/examples/장부_예시.xlsx",
            download_label="장부 예시 파일 받기",
        ),
        "overdue": example_block(
            "연체료 목록",
            table_html=_table(
                ["반납일(입금일)", "대여물품", "성명", "연체료", "비고"],
                [
                    ["2026-03-23", "보조배터리 1개", "김하늘", "4,000", ""],
                    ["2026-03-31", "폴라로이드 필름", "이도윤", "7,000", "필름값"],
                    ["2026-04-01", "앰프 1개", "박서연", "9,000", "최지호'으로 입금"],
                ],
            ),
            note="꼭 필요한 열은 대여물품, 성명, 연체료이고 반납일과 비고는 있으면 씁니다.",
            download_href="/examples/연체료목록_예시.xlsx",
            download_label="연체료 목록 예시 파일 받기",
        ),
        "refund": example_block(
            "환불자 목록",
            table_html=_table(
                ["사물함 번호", "이름", "환불금액", "사유"],
                [["전자-12", "정예린", "4,000", "당첨등록포기"], ["일반-05", "강민준", "2,000", "두번송금"]],
            ),
            note="꼭 필요한 열은 이름과 사유이고, 사물함 번호와 환불금액은 있으면 씁니다. 전화번호·계좌번호 열은 넣지 마세요.",
            download_href="/examples/환불자목록_예시.xlsx",
            download_label="환불자 목록 예시 파일 받기",
        ),
    }


# ── 예시 파일 받기용 엑셀 ──
LEDGER_SHEET = "장부"

ALLOWED_EXAMPLES = frozenset(
    {
        "장부_예시.xlsx",
        "연체료목록_예시.xlsx",
        "환불자목록_예시.xlsx",
    }
)


def ensure_example_files(examples_dir: Path) -> None:
    examples_dir.mkdir(parents=True, exist_ok=True)
    _write_ledger_sample(examples_dir / "장부_예시.xlsx")
    _write_overdue_sample(examples_dir / "연체료목록_예시.xlsx")
    _write_refund_sample(examples_dir / "환불자목록_예시.xlsx")


def resolve_example_path(examples_dir: Path, filename: str) -> Path | None:
    if filename not in ALLOWED_EXAMPLES:
        return None
    path = examples_dir / filename
    if not path.exists():
        ensure_example_files(examples_dir)
    return path if path.exists() else None


def _write_ledger_sample(path: Path) -> None:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = LEDGER_SHEET
    ws.append(
        [
            "날짜",
            "사업번호",
            "세부번호",
            "기재내용",
            "현금",
            "구분",
            "개요",
            "수입",
            "지출",
            "오류여부",
        ]
    )
    ws.append(
        [
            "2026-03-10",
            2,
            1,
            "전자12홍길동",
            "",
            "지출의환급",
            "1학기 대면 사물함 배부 사업 수익금 입금",
            4000,
            0,
            "",
        ]
    )
    ws.append(
        [
            "2026-03-23",
            3,
            1,
            "김하늘",
            "",
            "지출의환급",
            "1학기 물품 대여 사업",
            4000,
            0,
            "",
        ]
    )
    wb.save(path)
    wb.close()


def _write_overdue_sample(path: Path) -> None:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "연체료목록"
    ws.append(["반납일(입금일)", "대여물품", "성명", "연체료", "비고"])
    ws.append(["2026-03-23", "보조배터리 1개", "김하늘", 4000, ""])
    ws.append(["2026-03-31", "폴라로이드 필름", "이도윤", 7000, "필름값"])
    ws.append(["2026-04-01", "앰프 1개", "박서연", 9000, "최지호'으로 입금"])
    wb.save(path)
    wb.close()


def _write_refund_sample(path: Path) -> None:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "환불자목록"
    ws.append(["사물함 번호", "이름", "환불금액", "사유"])
    ws.append(["전자-12", "정예린", 4000, "당첨등록포기"])
    ws.append(["일반-05", "강민준", 2000, "두번송금"])
    wb.save(path)
    wb.close()
