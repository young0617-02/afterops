# 客服首次响应 SLA

- 验证时间：2026-11-05
- 范围：客服接管后未回复顾客的提醒，以及坐席绩效中的首次响应指标
- 依赖：已有 `handoff_agent_replied` 审计与 `handoff_first_response_minutes` 配置；无新迁移

## 行为

1. 工单创建后，客服第一次发送会话消息会写 `handoff_agent_replied` 审计；首次响应耗时按「工单创建时间 → 第一条客服回复时间」计算。
2. worker 每轮 SLA 扫描已接管（`claimed`）且创建超过 `HANDOFF_FIRST_RESPONSE_MINUTES`（默认 30 分钟）的工单；没有客服回复审计的，向当前接管人发一条站内通知，并写 `handoff_first_response_sla_reminder` 审计（含工单、等待分钟、优先级、接管人）。
3. 同一工单只提醒一次；有客服回复审计的工单不提醒。未接管工单仍由原有 5/10/15 分钟接管 SLA 提醒负责。
4. 坐席绩效新增：首次响应数、平均首次响应分钟、首次响应 SLA 超时。首次响应归因取审计里的 `operator_user_id`；超时统计窗口内被该坐席接管的工单中，无首响或首响超过阈值的数量。
5. 团队页绩效表新增「平均首响（次数）」与「首响 SLA 超时」两列。
6. 提醒阈值可由 `HANDOFF_FIRST_RESPONSE_MINUTES` 调整；worker 扫描周期沿用 `SLA_REMINDER_INTERVAL_SECONDS`（默认 300 秒）。

## 验证结果

| 项 | 结果 |
|---|---|
| 后端测试 | `tests/test_sla.py`：已接管 40 分钟未回复提醒一次、通知接管人、写审计、重复扫描不重发；已有客服回复的第二张工单不提醒。`tests/test_agent_performance_api.py`：首响 2 条平均 25 分钟、1 条超时；店主 1 条 2 分钟、无超时 |
| 后端全量 | `uv run pytest -q`：**227 passed**；`ruff check app tests` 通过 |
| 前端检查 | `npm run typecheck`、Docker `npm run build` 通过 |
| 前端 E2E | `npm run test:e2e`：**29 passed**；团队绩效页显示「平均首响」「首响 SLA 超时」 |
| 任务评测 | `uv run python -m app.scripts.task_eval`：19/19 通过 |
| 审计链 | `uv run python -m app.scripts.verify_audit`：merchant=5 OK |
| 容器 | api/worker/web 已重建；Compose 10 个服务均 Up，api/postgres/mailpit 为 healthy |

## 边界

- 首次响应从工单创建时间算起，不区分工作时间/节假日，也不按工单优先级分档；默认统一 30 分钟。
- 只提醒已接管工单；未接管工单仍走接管 SLA，不额外发首响提醒。
- 提醒只发给当前接管人，不升级给店主、不自动改派，也没有多次催办。
- 首次响应与耗时依赖 `handoff_agent_replied` 审计时间，精度到扫描时刻；不是消息级实时计时。
- 已解决但没有客服回复的工单不会被首响提醒（已结束），但在绩效窗口内仍会计为首次响应超时。
- 平均首响只统计窗口内有客服回复的工单；首次响应归因使用回复审计的操作人，工单后续改派不影响历史归因。
