"""Deterministic safety checks for the read-only assistant.

The first assistant iteration uses controlled templates. These checks still run
on user input (high-risk intent) and on the final reply (commitment leakage),
so the safety boundary is explicit before an LLM rewrite layer is added.
"""
from __future__ import annotations

import re

HIGH_RISK_RE = re.compile(
    r"保证|承诺|赔偿|赔付|投诉|12315|12345|必须退|给我退|全额退|价保|补差价|"
    r"退差价|我要退款|申请退款|退款吧|直接退|曝光|律师"
)

COMMITMENT_RE = re.compile(
    r"(?<!不)保证(?:能|可以|会|退|赔)|一定能|肯定能|已为您(?:退款|赔付|办理)|"
    r"已(?:退款|赔付)|今天(?:到|送达)|明天(?:到|送达)|包退|包赔|直接退|"
    r"全额退(?:给|款)"
)


def is_high_risk(text: str) -> bool:
    return bool(HIGH_RISK_RE.search(text))


def has_commitment(text: str) -> bool:
    return bool(COMMITMENT_RE.search(text))


SAFE_FALLBACK = "为避免作出未经核定的承诺，我只说明规则并转人工。"
