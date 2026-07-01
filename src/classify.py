"""구분 재계산 (레거시) — 장부 F열을 그대로 쓰므로 파이프라인에서는 사용하지 않음."""
from __future__ import annotations

# 하위 호환: 기존 코드·테스트에서 참조할 수 있는 기본 역할 매핑
LOCKER_BUSINESSES = {1, 2, 9}
RENTAL_BUSINESS = 3


def classify_transaction(
    role: str,
    is_income: bool,
    overview: str | None,
) -> tuple[str, bool]:
    """
    거래 성격(사업 역할)에 따라 구분을 반환한다.

    Args:
        role: student_fee | locker | rental | general
        is_income: 수입 여부
        overview: 장부 개요(G열)

    Returns:
        (구분, 검수필요 여부)
    """
    overview_text = overview or ""

    if role == "student_fee" and is_income:
        return "학생회비", False

    if role == "locker":
        if is_income:
            if "수익금" in overview_text or "입금" in overview_text:
                return "지출의환급", False
            return "", True
        if "환불" in overview_text:
            return "사업진행비", False
        return "사업진행비", False

    if role == "rental":
        if is_income:
            return "지출의환급", False
        return "사업진행비", False

    if not is_income:
        return "사업진행비", False

    return "", True


def classify_by_business_no(
    business_no: int | None,
    is_income: bool,
    overview: str | None,
) -> tuple[str, bool]:
    """레거시 사업번호 매핑으로 구분 (학기 설정 없을 때)."""
    if business_no is None:
        return "", True

    if business_no == 0:
        return classify_transaction("student_fee", is_income, overview)
    if business_no in LOCKER_BUSINESSES:
        return classify_transaction("locker", is_income, overview)
    if business_no == RENTAL_BUSINESS:
        return classify_transaction("rental", is_income, overview)
    return classify_transaction("general", is_income, overview)
