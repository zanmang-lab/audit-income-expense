"""검수 항목 3단계 — 화면·검수목록·체크리스트가 모두 같은 이름을 쓴다.

- 꼭 고칠 것: 문서에서 빠졌거나 비어 있거나 틀렸을 수 있음
- 확인할 것: 프로그램이 계산하거나 바꿔 적음(맞는지 한 번 보기)
- 참고: 알고만 있으면 됨
"""
from __future__ import annotations

MUST = "꼭 고칠 것"
CHECK = "확인할 것"
NOTE = "참고"
LEVELS = (MUST, CHECK, NOTE)
LEVEL_HELP = {
    MUST: "문서에서 빠졌거나 비어 있거나 틀렸을 수 있는 곳 — 확인하고 고치세요",
    CHECK: "프로그램이 계산하거나 바꿔 적은 곳 — 맞는지 한 번 보세요",
    NOTE: "알고만 있으면 되는 것",
}


def level_of(item) -> str:
    """ReviewItem·InputIssue 공통: auto → 확인할 것, advisory → 참고, 나머지 → 꼭 고칠 것."""
    if getattr(item, "auto", False):
        return CHECK
    if getattr(item, "advisory", False):
        return NOTE
    return MUST
