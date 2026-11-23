# 审批与工单超时提醒

> 后续更新：工单 SLA 已按优先级分层（紧急 5 分钟 / 高 10 分钟 / 普通 15 分钟），见 [handoff-priority.md](handoff-priority.md)。本页数字为当时快照。

- 验证时间：2026-11-03
- 范围：pending 提案与未接管人工工单的 SLA 提醒
- 执行：worker 定时任务

## 规则

- 待审提案创建超过 `PROPOSAL_SLA_MINUTES`（默认 30 分钟）：通知所有启用客服，并写 outbox 邮件。
- `open` 工单创建超过 `HANDOFF_SLA_MINUTES`（默认 15 分钟）：通知所有启用客服，并写 outbox 邮件；已接管工单不提醒。
- 用审计事件 `proposal_sla_reminder` / `handoff_sla_reminder` 去重，同一对象只提醒一次。
- Worker 巡检间隔 `SLA_REMINDER_INTERVAL_SECONDS`（默认 300 秒），启动即执行一次。

## 验证结果

| 项 | 结果 |
|---|---|
| 后端测试 | `tests/test_sla.py`：超时提案+未接管工单各提醒一次、重复执行不重复、审计去重 |
| 后端全量 | `uv run pytest -q`：**122 passed**；`ruff` 通过 |
| 前端 E2E | `npm run test:e2e`：10 passed |
| 任务评测 | `uv run python -m app.scripts.task_eval`：14/14 通过 |

## 边界

- 只提醒一次，不做升级/重复催办；没有按客服分配或在线状态。
- 只提醒 `open` 工单，`claimed` 处理中的工单不提醒。
- 时间阈值是全局配置，不支持按队列/SKU/金额配置。
- 依赖单 worker 与站内/邮件通道；无短信/IM。
