"""연체료 규정 파싱 및 물품 단가 조회."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field


@dataclass
class CategoryRule:
    per_day: int
    keywords: list[str] = field(default_factory=list)


@dataclass
class OverdueRulesConfig:
    default_per_day: int
    categories: list[CategoryRule] = field(default_factory=list)


def lookup_per_day(base_name: str, rules: OverdueRulesConfig) -> int:
    """물품명에 대해 keywords 부분일치 → per_day, 없으면 default.

    여러 키워드가 맞으면 **더 구체적인(긴) 키워드**를 쓴다.
    예: '돗자리(소) 1개'는 '돗자리'(3,000원)가 아니라 '돗자리(소)'(2,000원).
    괄호를 뺀 키워드('돗자리(대)'→'돗자리')로 맞은 경우는 괄호까지 맞은 경우보다 뒤로 둔다.
    """
    best: tuple[int, int] | None = None  # (점수, 단가)
    for category in rules.categories:
        for keyword in category.keywords:
            score = None
            if keyword and keyword in base_name:
                score = len(keyword) * 2 + 1
            else:
                bare = re.sub(r"\([^)]*\)", "", keyword).strip()
                if bare and bare in base_name:
                    score = len(bare) * 2
            if score is not None and (best is None or score > best[0]):
                best = (score, category.per_day)
    return best[1] if best else rules.default_per_day


def parse_overdue_rules_data(data: dict) -> OverdueRulesConfig:
    if "default_per_day" not in data:
        raise ValueError("연체료 규정 JSON에 default_per_day가 필요합니다.")

    categories: list[CategoryRule] = []
    for item in data.get("categories", []):
        categories.append(
            CategoryRule(
                per_day=int(item["per_day"]),
                keywords=list(item.get("keywords", [])),
            )
        )

    return OverdueRulesConfig(
        default_per_day=int(data["default_per_day"]),
        categories=categories,
    )


def parse_overdue_rules_json(text: str) -> OverdueRulesConfig:
    return parse_overdue_rules_data(json.loads(text))


def parse_business_mapping_json(text: str) -> dict[str, str]:
    """사업번호→사업명 JSON. {"names": {...}} 또는 flat dict 모두 허용."""
    data = json.loads(text)
    if isinstance(data, dict) and "names" in data:
        mapping = data["names"]
    else:
        mapping = data
    if not isinstance(mapping, dict):
        raise ValueError("사업명 매핑은 JSON 객체여야 합니다.")
    return {str(k): str(v) for k, v in mapping.items()}
