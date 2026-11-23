"""生成层：不引入 LangChain，自研轻量 provider 抽象（方案 §五「为什么自己写」）。

三种 provider，用 .env 的 LLM_PROVIDER 切换：
  template —— 默认。确定性模板落地：把门禁批准的原子句原样拼接，**零外部调用**，
              全部测试与离线评测走这条路径（可复现、可复跑、无需密钥）。
  ollama    —— 本机 Ollama /api/generate。
  openai    —— 任意 OpenAI 兼容 /chat/completions。

模型只允许「改写措辞」，不允许引入新的事实或数字：数字必须来自 GenerationPlan 的
allowed_numbers，由 gates.output_scan 在输出前复核（A9 的第二道锁）。
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from src.config import Settings, settings
from src.dialogue import estimate_tokens
from src.schemas import GenerationPlan

SYSTEM_INSTRUCTION = (
    "你是电商客服/导购助手。只允许用下面【允许输出的句子】改写措辞，不得新增任何"
    "事实、数字、金额、时效或承诺；不得替用户确认退款、赔付、价保补差等结论；"
    "必须保留转人工的说明。不得输出任何承诺类词汇（保证、一定能、已为您办理等）。"
)


@dataclass
class LLMRequest:
    plan: GenerationPlan
    user_text: str
    context_block: str = ""
    citations: list[str] = field(default_factory=list)

    @property
    def approved_text(self) -> str:
        return "\n".join(f"- ({a.kind.value}) {a.text}" for a in self.plan.atoms)


@dataclass
class LLMResult:
    text: str
    provider: str
    prompt_tokens: int
    completion_tokens: int
    error: str | None = None


class ProviderError(Exception):
    pass


class TemplateProvider:
    name = "template"

    def generate(self, req: LLMRequest, timeout_ms: int = 2000) -> LLMResult:
        text = "".join(a.text for a in req.plan.atoms)
        return LLMResult(text=text, provider=self.name,
                         prompt_tokens=estimate_tokens(req.approved_text),
                         completion_tokens=estimate_tokens(text))


class _HTTPProvider:
    def __init__(self, base_url: str, model: str, api_key: str = ""):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key

    def _post(self, path: str, payload: dict, timeout_ms: int) -> dict:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        req = urllib.request.Request(self.base_url + path, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=max(0.5, timeout_ms / 1000.0)) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise ProviderError(f"{self.name} 调用失败：{exc}") from exc

    def generate(self, req: LLMRequest, timeout_ms: int = 20000) -> LLMResult:
        raise NotImplementedError


class OllamaProvider(_HTTPProvider):
    name = "ollama"

    def __init__(self, base_url: str, model: str):
        super().__init__(base_url, model)
        self.name = "ollama"

    def generate(self, req: LLMRequest, timeout_ms: int = 60000) -> LLMResult:
        prompt = (f"{SYSTEM_INSTRUCTION}\n\n【允许输出的句子】\n{req.approved_text}\n\n"
                  f"【会话上文】\n{req.context_block}\n\n【本轮用户输入】\n{req.user_text}\n\n回复：")
        out = self._post("/api/generate",
                         {"model": self.model, "prompt": prompt, "stream": False,
                          "options": {"temperature": 0.2}}, timeout_ms)
        text = (out.get("response") or "").strip()
        if not text:
            raise ProviderError("ollama 返回空内容")
        return LLMResult(text=text, provider=self.name,
                         prompt_tokens=estimate_tokens(prompt), completion_tokens=estimate_tokens(text))


class OpenAICompatProvider(_HTTPProvider):
    name = "openai"

    def __init__(self, base_url: str, model: str, api_key: str):
        super().__init__(base_url, model, api_key)
        self.name = "openai"

    def generate(self, req: LLMRequest, timeout_ms: int = 60000) -> LLMResult:
        messages = [
            {"role": "system", "content": SYSTEM_INSTRUCTION},
            {"role": "user", "content": f"【允许输出的句子】\n{req.approved_text}\n\n"
                                        f"【会话上文】\n{req.context_block}\n\n"
                                        f"【本轮用户输入】\n{req.user_text}\n\n请按允许内容回复。"},
        ]
        out = self._post("/chat/completions",
                         {"model": self.model, "messages": messages, "temperature": 0.2}, timeout_ms)
        try:
            text = out["choices"][0]["message"]["content"].strip()
        except (KeyError, IndexError) as exc:
            raise ProviderError(f"响应结构异常：{out}") from exc
        usage = out.get("usage") or {}
        return LLMResult(text=text, provider=self.name,
                         prompt_tokens=int(usage.get("prompt_tokens") or estimate_tokens(str(messages))),
                         completion_tokens=int(usage.get("completion_tokens") or estimate_tokens(text)))


def get_provider(cfg: Settings | None = None):
    cfg = cfg or settings()
    if cfg.llm_provider == "ollama":
        if not cfg.ollama_model:
            raise ProviderError("LLM_PROVIDER=ollama 但未设置 OLLAMA_MODEL（.env.example 里该项是待填占位）")
        return OllamaProvider(cfg.ollama_base_url, cfg.ollama_model)
    if cfg.llm_provider == "openai":
        if not (cfg.openai_api_key and cfg.openai_model and cfg.openai_base_url):
            raise ProviderError("LLM_PROVIDER=openai 但 OPENAI_API_KEY/OPENAI_MODEL/OPENAI_BASE_URL 未配齐")
        return OpenAICompatProvider(cfg.openai_base_url, cfg.openai_model, cfg.openai_api_key)
    return TemplateProvider()
