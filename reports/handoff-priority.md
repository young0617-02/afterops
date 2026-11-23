# 人工工单优先级与分诊

- 验证时间：2026-11-04
- 范围：转人工工单优先级、自动升级、后台排序筛选、概览紧急计数
- 迁移：`7f4351940994`（`handoff_tickets.priority`）

## 规则

| 场景 | 优先级 |
|---|---|
| 投诉 / 举报 / 12315 等 `COMPLAINT` | 紧急 |
| 承诺索取、赔付施压等 `HIGH_RISK` | 高优先级 |
| 其他显式转人工 / 普通售后 | 普通 |

- 同一会话已有活动工单时，后续高危消息可自动升级，且不会降级。
- 升级写 `handoff_priority_escalated` 审计，包含 from/to。
- 后台工单列表按紧急 → 高 → 普通排序，可按优先级筛选。
- 概览「待接管工单」卡片显示处理中与紧急数量。
- SLA 提醒按优先级分层：紧急 `HANDOFF_SLA_URGENT_MINUTES`（默认 5 分钟）、高 `HANDOFF_SLA_HIGH_MINUTES`（默认 10 分钟）、普通 `HANDOFF_SLA_MINUTES`（默认 15 分钟）；worker 巡检间隔不变，审计去重逻辑不变。

## 验证结果

| 项 | 结果 |
|---|---|
| 迁移 | `upgrade → downgrade -1 → upgrade` 通过，当前 head `7f4351940994` |
| 后端测试 | `tests/test_handoffs_api.py`：普通→投诉升级 urgent、独立高风险 high、排序、priority 筛选、概览 urgent 计数、审计链；`tests/test_sla.py`：紧急 5 分钟先提醒、高 10 分钟随后提醒、普通 15 分钟不提前提醒 |
| 后端全量 | `uv run pytest -q`：**156 passed**；`ruff` 通过 |
| 前端 E2E | `npm run test:e2e`：**17 passed**；投诉转人工后后台工单显示「紧急」，按紧急筛选可见 |
| 任务评测 | `uv run python -m app.scripts.task_eval`：18/18 通过 |
| 审计链 | `uv run python -m app.scripts.verify_audit`：OK |

## 边界

- 优先级基于意图/队列规则，不是模型置信度或人工评级；后台暂不支持手动改级。
- 不做自动分配、坐席在线状态或按优先级调整 SLA 阈值；SLA 提醒仍是统一时限。
- 同一会话只在更高优先级消息时升级，不降级；解决后的新工单重新按规则计算。
- 普通售后消息不会因等待时长自动升级。
