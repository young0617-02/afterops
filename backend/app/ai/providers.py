"""LLM providers used only to rewrite already-approved replies.

The deterministic templates remain the source of truth. A provider may change
wording, but the caller rejects any output with commitments, unapproved numbers,
or a missing human handoff.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass

from app.settings import Settings, settings

SYSTEM_INSTRUCTION = (
    "你是电商售后助手。只允许改写【已批准内容】的措辞，不得新增任何事实、数字、"
    "金额、时效或承诺；不得替用户确认退款、赔付、补差等结论；必须保留已批准内容中的"
    "全部数字、订单号、金额、状态和转人工说明，不得省略。"
    "不得输出保证、一定能、已退款、已赔付、已办理等承诺词。"
)


class ProviderError(Exception):
    pass


@dataclass(frozen=True)
class LLMRequest:
    approved_text: str
    user_text: str
    context_block: str = ""


@dataclass(frozen=True)
class LLMResult:
    text: str
    provider: str
    error: str | None = None


class TemplateProvider:
    name = "template"

    def generate(self, req: LLMRequest) -> LLMResult:
        return LLMResult(text=req.approved_text, provider=self.name)


class _HTTPProvider:
    name = "http"

    def __init__(self, base_url: str, model: str, api_key: str = "", timeout_ms: int = 20000):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout_ms = timeout_ms

    def _post(self, path: str, payload: dict) -> dict:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        request = urllib.request.Request(
            self.base_url + path, data=data, headers=headers, method="POST"
        )
        try:
            with urllib.request.urlopen(
                request, timeout=max(0.5, self.timeout_ms / 1000.0)
            ) as response:
                return json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise ProviderError(f"{self.name} 调用失败：{exc}") from exc

    def generate(self, req: LLMRequest) -> LLMResult:
        raise NotImplementedError


class OllamaProvider(_HTTPProvider):
    name = "ollama"

    def generate(self, req: LLMRequest) -> LLMResult:
        prompt = (
            f"{SYSTEM_INSTRUCTION}\n\n【已批准内容】\n{req.approved_text}\n\n"
            f"【用户问题】\n{req.user_text}\n\n请只输出改写后的客服回复："
        )
        payload = self._post(
            "/api/generate",
            {
                "model": self.model,
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": 0.2},
            },
        )
        text = (payload.get("response") or "").strip()
        if not text:
            raise ProviderError("ollama 返回空内容")
        return LLMResult(text=text, provider=self.name)


class OpenAICompatProvider(_HTTPProvider):
    name = "openai"

    def generate(self, req: LLMRequest) -> LLMResult:
        payload = self._post(
            "/chat/completions",
            {
                "model": self.model,
                "temperature": 0.2,
                "messages": [
                    {"role": "system", "content": SYSTEM_INSTRUCTION},
                    {
                        "role": "user",
                        "content": (
                            f"【已批准内容】\n{req.approved_text}\n\n"
                            f"【用户问题】\n{req.user_text}\n\n请只输出改写后的客服回复。"
                        ),
                    },
                ],
            },
        )
        try:
            text = payload["choices"][0]["message"]["content"].strip()
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError(f"openai 响应结构异常：{payload}") from exc
        if not text:
            raise ProviderError("openai 返回空内容")
        return LLMResult(text=text, provider=self.name)


def get_provider(cfg: Settings | None = None):
    cfg = cfg or settings
    provider = cfg.llm_provider.lower()
    if provider == "template":
        return TemplateProvider()
    if provider == "ollama":
        if not cfg.ollama_model:
            raise ProviderError("LLM_PROVIDER=ollama 但未设置 OLLAMA_MODEL")
        return OllamaProvider(
            cfg.ollama_base_url, cfg.ollama_model, timeout_ms=cfg.llm_timeout_ms
        )
    if provider == "openai":
        if not (cfg.openai_base_url and cfg.openai_api_key and cfg.openai_model):
            raise ProviderError(
                "LLM_PROVIDER=openai 但 OPENAI_BASE_URL/OPENAI_API_KEY/OPENAI_MODEL 未配齐"
            )
        return OpenAICompatProvider(
            cfg.openai_base_url,
            cfg.openai_model,
            api_key=cfg.openai_api_key,
            timeout_ms=cfg.llm_timeout_ms,
        )
    raise ProviderError(f"未知 LLM_PROVIDER={cfg.llm_provider}")
