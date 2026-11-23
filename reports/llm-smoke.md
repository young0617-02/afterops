# 真实模型改写 smoke（Ollama）

- 验证时间：2026-11-02
- 模型：本机 Ollama `qwen2.5:7b`（Q4_K_M）
- 范围：已批准回复的措辞改写与输出门禁，不改变事实来源
- 脚本：`uv run python -m app.scripts.llm_smoke`

## 运行方式

```bash
# 本机 API 进程
LLM_PROVIDER=ollama OLLAMA_MODEL=qwen2.5:7b \
  OLLAMA_BASE_URL=http://127.0.0.1:11434 \
  uv run python -m app.scripts.llm_smoke

# 容器内 API（访问宿主机 Ollama）
docker exec \
  -e LLM_PROVIDER=ollama \
  -e OLLAMA_MODEL=qwen2.5:7b \
  -e OLLAMA_BASE_URL=http://host.docker.internal:11434 \
  afterops-api-1 python -m app.scripts.llm_smoke
```

## 结果

| 路径 | 用例 | 结果 | 延迟 |
|---|---|---|---|
| 本机 API | `SKU-EAR-B2 多少钱` | PASS，`provider=ollama / degraded=false`，129.00 与库存数保留 | 约 1.5s |
| 本机 API | `转人工` | PASS，`degraded=false`，保留「回复「转人工」」 | 约 1.1s |
| API 容器 | 同上 2 条 | PASS，经 `host.docker.internal:11434` 访问宿主机模型 | 约 1–2s |

示例输出（商品）：

> 商品半入耳真无线耳机 B2（SKU-EAR-B2）页面价为 129.00 元，当前可售 24 件。库存为查询时点快照，价格以提交订单时页面展示为准。如需人工，回复「转人工」。

## 验证中发现并修复

| 问题 | 修复 |
|---|---|
| `get_provider()` 把 `settings` 实例当函数调用，默认配置路径直接 TypeError | 改为 `cfg = cfg or settings`，新增默认 template 测试 |
| 门禁只检查输出是否含「转人工」子串，模型用「已记录您的转人工请求」即可绕过可执行出口 | 改为正则要求「回复/联系/转接… + 人工」；模板回复也按同一规则补出口 |
| 门禁只拦截新增数字，不拦截遗漏已批准数字（模型曾省略库存数） | 增加 `missing_numbers` 回退；提示词要求保留全部数字、订单号、金额、状态 |
| 模型改写会省略人工出口说明 | 系统提示词要求保留；门禁失败时回退模板 |

## 默认与边界

- 默认仍为 `template`；Docker Compose 可通过 `LLM_PROVIDER=ollama OLLAMA_MODEL=...` 显式切换，未默认开启。
- 只验证 2 条改写用例，不代表整体回答质量、延迟分布、并发或成本。
- 未测 OpenAI-compatible 真实服务；未做模型选择、采样、上下文长度或降级策略调优。
- 模型输出仍可能被门禁拒绝并回退模板；这是设计行为，不是链路故障。
- 本次不计入任务级 11 条评测，也不构成生产 SLA。
