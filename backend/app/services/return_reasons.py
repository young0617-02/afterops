from __future__ import annotations

from app.services.errors import ValidationError

REASON_LABELS = {
    "quality": "质量问题",
    "damaged": "运输破损",
    "wrong_item": "发错/少发",
    "size": "尺寸不合适",
    "not_needed": "不想要",
    "other": "其他",
}
REASON_CATEGORIES = tuple(REASON_LABELS)
DEFAULT_REASON_CATEGORY = "other"

_KEYWORDS = (
    ("wrong_item", ("发错", "错货", "少发", "漏发", "不是我买")),
    ("damaged", ("破损", "摔坏", "压坏", "包装破", "运输损")),
    ("size", ("尺寸", "尺码", "大小", "不合", "太紧", "太松")),
    (
        "quality",
        ("质量", "坏", "故障", "无声", "杂音", "破音", "不工作", "失灵", "异响"),
    ),
    ("not_needed", ("不想要", "不喜欢", "买错", "多买", "无理由")),
)


def classify_return_reason(text: str) -> str:
    for category, keywords in _KEYWORDS:
        if any(keyword in text for keyword in keywords):
            return category
    return DEFAULT_REASON_CATEGORY


def normalize_reason_category(value: str) -> str:
    category = value.strip() or DEFAULT_REASON_CATEGORY
    if category not in REASON_LABELS:
        raise ValidationError("未知退货原因分类")
    return category