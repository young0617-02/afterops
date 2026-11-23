# 低分满意度自动回访

- 验证时间：2026-11-05
- 范围：1–2 星工单评价自动创建高优先级回访工单，复用现有工单队列
- 迁移：`d5f7a9c1e3b6`（`handoff_tickets.follow_up_for_ticket_id`）

## 行为

1. 顾客对已解决工单提交 1–2 星评价时，在原有低分告警之外，自动创建一条新 `HandoffTicket`：`queue=complaint`、`priority=high`、`intent=LOW_CSAT`、`status=open`。
2. 新工单通过 `follow_up_for_ticket_id` 指向原工单，`last_user_message` 带原工单号、评分与评论；会话状态重新置为 `handoff`，AI 在回访工单处理期间暂停自动答复。
3. 同一会话已有未解决工单时不重复创建；评价本身仍只允许一次，因此每张已解决工单最多产生一条低分回访。
4. 写 `low_csat_follow_up_created` 审计（原工单、回访工单、评价 ID、评分），原 `low_csat_alert` 审计与员工通知保留。
5. 评分接口返回 `follow_up_ticket_ref`；顾客售后进度立即出现新的「低分回访」工单，提交评价提示中显示回访单号。
6. 客服按现有流程接管、回复、解决回访工单；回复进入原会话并通知顾客，解决后可再次评价（新工单独立评价）。
7. 前端补充「低分回访」意图标签；后台队列显示投诉队列、高优先级与回访来源消息。

## 验证结果

| 项 | 结果 |
|---|---|
| 后端测试 | `tests/test_satisfaction_api.py`：5 星不建回访、返回空引用；2 星创建工单字段/原工单引用/会话重开/审计/顾客列表；店主接管并回复后顾客可见；原有低分通知 2 条不变 |
| 后端全量 | `uv run pytest -q`：**266 passed**；`ruff check app tests` 通过 |
| 前端检查 | `npm run typecheck`、Docker `npm run build` 通过 |
| 前端 E2E | `npm run test:e2e`：**34 passed**；新增「2 星评价 → 生成回访工单 → 店主接管回复 → 顾客看到回复」 |
| 迁移 | 真实 PostgreSQL `upgrade head` → `downgrade -1` → `upgrade head` 通过 |
| 任务评测 | `uv run python -m app.scripts.task_eval`：19/19 通过 |
| 审计链 | `uv run python -m app.scripts.verify_audit`：merchant=5 OK |
| 容器 | api/web/worker 已重建；10 个服务 Up，api/postgres/mailpit 为 healthy |

## 边界

- 回访工单不自动指派给原客服，也不自动分配；仍进入待接管队列由店主/客服接管。
- 没有回访时限、回访结果字段、外呼/短信或一次性关闭原评价的流程；仍靠工单状态与回复留痕。
- 只在 1–2 星时创建；3 星不触发回访，也没有按坐席/类目配置阈值。
- 同一会话已有未解决工单时不新建回访，低分告警仍会发送。
- 回访工单是普通投诉队列工单，没有单独的「回访」筛选或统计口径；团队 CSAT 仍只统计原评价。
- 顾客评价不可修改或撤销；回访解决后可对新工单重新评价。
