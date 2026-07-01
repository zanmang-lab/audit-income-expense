"""업로드 폼용 예시 양식 HTML 조각 (장부·연체료·환불자 목록)."""


def example_block(
    title: str,
    *,
    table_html: str,
    download_href: str | None = None,
    download_label: str | None = None,
    extra_html: str = "",
) -> str:
    download = ""
    if download_href and download_label:
        download = (
            f'<p class="mt-3">'
            f'<a href="{download_href}" download class="text-primary text-label-md font-label-md hover:underline font-semibold">'
            f"{download_label}</a></p>"
        )
    return f"""<details class="mt-4 group" open>
  <summary class="text-primary text-label-md font-label-md cursor-pointer hover:underline flex items-center gap-1 list-none select-none">
    <span class="material-symbols-outlined text-sm transition-transform group-open:rotate-180">expand_more</span>
    {title} — 예시 양식
  </summary>
  <div class="mt-3 overflow-x-auto rounded-lg border border-outline-variant bg-white">
    {table_html}
  </div>
  {download}
  {extra_html}
</details>"""


def upload_form_examples_html() -> dict[str, str]:
    return {
        "ledger": example_block(
            "장부",
            table_html=_ledger_table(),
            download_href="/examples/장부_예시.xlsx",
            download_label="장부 예시.xlsx 다운로드",
        ),
        "overdue": example_block(
            "연체료 목록",
            table_html=_overdue_table(),
            download_href="/examples/연체료목록_예시.xlsx",
            download_label="연체료 목록 예시.xlsx 다운로드",
        ),
        "refund": example_block(
            "환불자 목록",
            table_html=_refund_table(),
            download_href="/examples/환불자목록_예시.xlsx",
            download_label="환불자 목록 예시.xlsx 다운로드",
            extra_html=(
                '<p class="mt-2 text-xs text-error flex items-center gap-1">'
                '<span class="material-symbols-outlined text-sm">privacy_tip</span>'
                "전화번호·계좌번호 열은 올리지 마세요. 예시 파일도 이름·금액·사유만 포함합니다."
                "</p>"
            ),
        ),
    }


def _table_wrap(inner: str) -> str:
    return f'<table class="w-full text-xs text-left border-collapse">{inner}</table>'


def _ledger_table() -> str:
    return _table_wrap("""<thead class="bg-surface-container">
  <tr>
    <th class="p-2 border-b border-r border-outline-variant font-semibold">날짜</th>
    <th class="p-2 border-b border-r border-outline-variant font-semibold">사업번호</th>
    <th class="p-2 border-b border-r border-outline-variant font-semibold">세부번호</th>
    <th class="p-2 border-b border-r border-outline-variant font-semibold">기재내용</th>
    <th class="p-2 border-b border-r border-outline-variant font-semibold">구분</th>
    <th class="p-2 border-b border-r border-outline-variant font-semibold">개요</th>
    <th class="p-2 border-b border-r border-outline-variant font-semibold">수입</th>
    <th class="p-2 border-b border-outline-variant font-semibold">지출</th>
  </tr>
</thead>
<tbody>
  <tr>
    <td class="p-2 border-b border-r border-outline-variant">2026-03-10</td>
    <td class="p-2 border-b border-r border-outline-variant">2</td>
    <td class="p-2 border-b border-r border-outline-variant">1</td>
    <td class="p-2 border-b border-r border-outline-variant">전자49장문경</td>
    <td class="p-2 border-b border-r border-outline-variant">지출의환급</td>
    <td class="p-2 border-b border-r border-outline-variant">대면 사물함 수익금 입금</td>
    <td class="p-2 border-b border-r border-outline-variant">4,000</td>
    <td class="p-2 border-b border-outline-variant">0</td>
  </tr>
  <tr class="bg-surface-container-low">
    <td class="p-2 border-r border-outline-variant">2026-03-23</td>
    <td class="p-2 border-r border-outline-variant">3</td>
    <td class="p-2 border-r border-outline-variant">1</td>
    <td class="p-2 border-r border-outline-variant">김지민</td>
    <td class="p-2 border-r border-outline-variant">지출의환급</td>
    <td class="p-2 border-r border-outline-variant">물품 대여 연체료</td>
    <td class="p-2 border-r border-outline-variant">4,000</td>
    <td class="p-2 border-outline-variant">0</td>
  </tr>
</tbody>""") + (
        '<p class="p-3 text-xs text-on-surface-variant bg-surface-container-low border-t border-outline-variant">'
        "첫 행은 헤더, 2행부터 데이터입니다. 시트 이름은 자유롭고, 데이터가 있는 시트를 자동으로 읽습니다. "
        "<strong>구분(F열)</strong>은 장부 값을 그대로 수입지출내역서에 옮깁니다."
        "</p>"
    )


def _overdue_table() -> str:
    return _table_wrap("""<thead class="bg-surface-container">
  <tr>
    <th class="p-2 border-b border-r border-outline-variant font-semibold">반납일(입금일)</th>
    <th class="p-2 border-b border-r border-outline-variant font-semibold">대여물품</th>
    <th class="p-2 border-b border-r border-outline-variant font-semibold">성명</th>
    <th class="p-2 border-b border-r border-outline-variant font-semibold">연체료</th>
    <th class="p-2 border-b border-outline-variant font-semibold">비고</th>
  </tr>
</thead>
<tbody>
  <tr><td class="p-2 border-b border-r border-outline-variant">2026-03-23</td><td class="p-2 border-b border-r border-outline-variant">보조배터리 1개</td><td class="p-2 border-b border-r border-outline-variant">김지민</td><td class="p-2 border-b border-r border-outline-variant">4,000</td><td class="p-2 border-b border-outline-variant"></td></tr>
  <tr class="bg-surface-container-low"><td class="p-2 border-b border-r border-outline-variant">2026-03-31</td><td class="p-2 border-b border-r border-outline-variant">폴라로이드 필름</td><td class="p-2 border-b border-r border-outline-variant">하명진</td><td class="p-2 border-b border-r border-outline-variant">7,000</td><td class="p-2 border-b border-outline-variant">필름값</td></tr>
  <tr><td class="p-2 border-r border-outline-variant">2026-04-01</td><td class="p-2 border-r border-outline-variant">돗자리(대) 1개</td><td class="p-2 border-r border-outline-variant">서윤아</td><td class="p-2 border-r border-outline-variant">9,000</td><td class="p-2 border-outline-variant">조소영'으로 입금</td></tr>
</tbody>""")


def _refund_table() -> str:
    return _table_wrap("""<thead class="bg-surface-container">
  <tr>
    <th class="p-2 border-b border-r border-outline-variant font-semibold">사물함 번호</th>
    <th class="p-2 border-b border-r border-outline-variant font-semibold">이름</th>
    <th class="p-2 border-b border-r border-outline-variant font-semibold">환불금액</th>
    <th class="p-2 border-b border-outline-variant font-semibold">사유</th>
  </tr>
</thead>
<tbody>
  <tr><td class="p-2 border-b border-r border-outline-variant">전자-12</td><td class="p-2 border-b border-r border-outline-variant">이지원</td><td class="p-2 border-b border-r border-outline-variant">4,000</td><td class="p-2 border-b border-outline-variant">당첨등록포기</td></tr>
  <tr class="bg-surface-container-low"><td class="p-2 border-r border-outline-variant">일반-05</td><td class="p-2 border-r border-outline-variant">박민수</td><td class="p-2 border-r border-outline-variant">2,000</td><td class="p-2">두번송금</td></tr>
</tbody>""")
