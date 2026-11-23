# 工单自动分配

- 验证时间：2026-11-05
- 范围：店主开关、新工单按当前负载自动分配给启用客服
- 迁移：`f4b6d8e0c2a3`（`merchants.auto_assign_handoffs`）

## 行为

1. 商家级开关默认关闭；店主在后台「团队 → 工单分配」勾选「新工单自动分配」即时保存，客服不可修改。
2. 开启后，新转人工工单创建时统计所有启用客服（`role=agent`、`status=active`）当前 `open/claimed` 工单数，选择负载最少者；同负载按账号 ID 升序。
3. 被选中的客服自动接管：工单状态置 `claimed`，记录 `claimed_by/claimed_at`；写 `handoff_auto_assigned` 审计（工单、客服、当前负载、队列、优先级），并给该客服发站内通知。
4. 没有启用客服时保持 `open` 待接管；店主账号不参与自动分配。
5. 关闭开关后新工单恢复待接管；已分配工单不受影响。
6. 设置变更写 `handoff_assignment_updated` 审计（前后值、操作人）。
7. 接口：`GET/PATCH /api/admin/handoff-settings`，GET 店主/客服可读，PATCH 仅店主。

## 验证结果

| 项 | 结果 |
|---|---|
| 后端测试 | `tests/test_handoff_auto_assign.py`：默认关闭；开启后两张新工单依次分给两名客服（负载均衡），状态/接管人/审计/通知正确；关闭后新工单保持待接管；客服 PATCH 403 |
| 后端全量 | `uv run pytest -q`：**241 passed**；`ruff check app tests` 通过 |
| 前端检查 | `npm run typecheck`、Docker `npm run build` 通过 |
| 前端 E2E | `npm run test:e2e`：**31 passed**；团队页显示「新工单自动分配」设置 |
| 迁移 | 真实 PostgreSQL `upgrade head` → `downgrade -1` → `upgrade head` 通过 |
| 任务评测 | `uv run python -m app.scripts.task_eval`：19/19 通过 |
| 审计链 | `uv run python -m app.scripts.verify_audit`：merchant=5 OK |
| 容器 | api/web 已重建；10 个服务 Up，api/postgres/mailpit 为 healthy |

## 边界

- 只按当前 `open/claimed` 工单数做静态负载均衡，并排除手动标记为离开的客服；不看在线的实时心跳、技能、队列或工单优先级。
- 同负载固定按账号 ID 选择，不是严格轮询；并发创建两张工单可能同时选中同一客服。
- 只影响新工单；已有待接管工单不会自动重分配，也没有自动改派/回收。
- 店主账号不参与；只有一个启用且接单中的客服时所有新工单都会分给他；全部离开时保持待接管。
- 没有工作时间/节假日、最大并发上限或超载保护；客服可照常手动改派。
- 设置变更只写审计，没有变更历史页面或通知；没有按团队/队列配置不同策略。
