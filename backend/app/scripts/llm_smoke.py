"""Run approved-reply rewrites through the configured LLM provider.

This is an optional integration smoke, not a unit test: it requires a real
provider (for example local Ollama) and deliberately does not touch the database.

    LLM_PROVIDER=ollama OLLAMA_MODEL=qwen2.5:7b \
      uv run python -m app.scripts.llm_smoke
"""
from __future__ import annotations

import sys

from app.ai.chat import _rewrite_reply
from app.ai.providers import ProviderError, get_provider
from app.settings import settings

CASES = (
    {
        "name": "product_info",
        "user_text": "SKU-EAR-B2 多少钱",
        "approved": (
            "商品半入耳真无线耳机 B2（SKU-EAR-B2）页面价 129.00 元，"
            "当前可售 15 件。库存为查询时点快照，价格以提交订单时页面展示为准。"
            " 如需人工，回复「转人工」。"
        ),
    },
    {
        "name": "human_request",
        "user_text": "转人工",
        "approved": (
            "已记录转人工请求，本轮问题与查询到的数据会一并移交坐席。"
            " 如需人工，回复「转人工」。"
        ),
    },
)


def main() -> int:
    try:
        provider = get_provider(settings)
    except ProviderError as exc:
        print(f"[llm-smoke] provider config failed: {exc}")
        return 1
    print(
        f"[llm-smoke] provider={getattr(provider, 'name', 'unknown')} "
        f"model={settings.ollama_model or settings.openai_model or '-'}"
    )
    failed = False
    for case in CASES:
        try:
            text, name, degraded, reason = _rewrite_reply(
                case["approved"], case["user_text"], provider=provider
            )
        except ProviderError as exc:
            print(f"[FAIL] {case['name']} provider exception: {exc}")
            failed = True
            continue
        status = "PASS" if not degraded else "FALLBACK"
        print(
            f"[{status}] {case['name']} provider={name} "
            f"degraded={degraded} reason={reason or '-'}"
        )
        print(f"  {text}")
        if degraded:
            failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
