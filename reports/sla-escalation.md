# 首响 SLA 自动升级

- 验证时间：2026-11-05
- 范围：已接管工单首响超时后自动升级优先级并通知店主
- 迁移：`a7c9e1b3d5f8`（`handoff_tickets.escalated_at`）

## 行为

1. SLA worker 在原有首响提醒之外新增升级检查：工单 `claimed`、创建超过 `HANDOFF_FIRST_RESPONSE_ESCALATION_MINUTES`（默认 60 分钟）、仍无 `handoff_agent_replied` 审计且未升级过时触发。
2. 升级将普通优先级提升为 `high`（已是 high/urgent 保持原级），写入 `escalated_at`，通知该商家全部启用店主（站内通知 `handoff_first_response_escalation`）。
3. 每个工单只升级一次：`escalated_at` 与 `handoff_escalated` 审计双重去重；已回复、已解决、未接管（open）的工单不升级。
4. 审计记录工单、等待分钟、升级前后优先级、接管客服与通知店主列表；不改变接管人与工单状态。
5. 后台工单卡在优先级徽标旁展示「SLA 已升级」；工单 API 返回 `escalated_at`。
6. 默认阈值可通过 `HANDOFF_FIRST_RESPONSE_MINUTES` 与 `HANDOFF_FIRST_RESPONSE_ESCALATION_MINUTES` 配置，worker 读取环境变量。

## 验证结果

| 项 | 结果 |
|---|---|
| 后端测试 | `tests/test_sla.py`：创建 70 分钟无回复的已接管工单 → 首次响应提醒 1 次 + 升级 1 次、优先级 high、`escalated_at`、只通知店主、审计含优先级与店主列表；再次运行不重复升级；已有 SLA 用例（提醒一次、按优先级阈值、已回复不提醒）全部回归 |
| 后端全量 | `uv run pytest -q`：**269 passed**；`ruff check app tests` 通过 |
| 前端检查 | `npm run typecheck`、Docker `npm run build` 通过 |
| 前端 E2E | `npm run test:e2e`：**35 passed**（本功能为 60 分钟时间驱动，未新增实时 E2E，由后端时间推进测试覆盖；全量 E2E 回归通过） |
| 迁移 | 真实 PostgreSQL `upgrade head` → `downgrade -1` → `upgrade head` 通过 |
| 任务评测 | `uv run python -m app.scripts.task_eval`：19/19 通过 |
| 审计链 | `uv run python -m app.scripts.verify_audit`：merchant=5 OK |
| 容器 | api/web/worker 已重建；10 个服务 Up，api/postgres/mailpit 为 healthy |

## 边界

- 仅首响超时升级，没有解决超时自动升级；解决 SLA 仍只提醒接管人。
- 不自动改派、不做多级升级链/值班表/电话或短信外呼；只发站内通知给启用店主。
- 商家没有启用店主账号时不会升级；工单保持原优先级。
- 按自然分钟计算，不区分工作时间/节假日，也没有暂停或延后机制。
- 优先级只提升到 high，不改为 urgent；升级后不会因后续回复自动降级。
- 客户侧看不到升级动作，也没有「已加急」提示；没有升级筛选/统计页面。
