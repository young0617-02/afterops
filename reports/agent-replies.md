# 人工客服会话回复

- 验证时间：2026-11-05
- 范围：客服在人工工单内直接回复顾客，消息进入原会话；人工处理期间 AI 停止自动答复
- 依赖：消息角色新增 `agent`（迁移 `c7d2a4e91b30`）

## 行为

1. 客服在后台「人工工单 → 查看会话」中看到完整消息记录，可输入不超过 1000 字的回复并发送。
2. 接口 `POST /api/admin/handoffs/{ticket_id}/messages` 仅店主/客服可用；工单必须属于当前商家。
3. 未接管工单在首次回复时自动接管：工单置为 `claimed`、记录接管人和时间，并写 `handoff_claimed`（`auto=true`）审计。
4. 已被其他客服接管的工单拒绝回复（409）；已解决工单拒绝回复（409）。回复人必须是当前接管人。
5. 回复以 `role=agent` 写入 `messages`，前台聊天记录标注「客服」并使用独立气泡样式；工单 `last_reply` 更新为客服回复内容，列表刷新后可见。
6. 每次回复给顾客写站内通知「客服回复了工单 TK-…」，按售后邮件偏好进入 outbox；写 `handoff_agent_replied` 审计（含消息 ID、是否自动接管、长度）。
7. 存在 `open/claimed` 工单时，顾客继续发言不再触发 AI 查询/提案：消息落库并更新工单最近顾客消息，返回「已转人工/客服已接管」等待提示，写 `chat_handoff_message` 审计；投诉/高危关键词仍会升级工单队列与优先级。
8. 工单解决、会话关闭后，顾客再次发言恢复 AI 自动答复。
9. 顾客聊天窗保持打开时每 5 秒增量拉取新消息（`after_id` 游标），客服回复会自动出现在当前会话；关闭后重新打开同样加载最新历史。没有 WebSocket/SSE 推送。

## 验证结果

| 项 | 结果 |
|---|---|
| 后端测试 | `tests/test_handoff_replies.py`：店主首次回复自动接管、消息角色序列 user/assistant/agent、顾客会话可见、通知与审计正确；人工处理中顾客消息走 `HANDOFF_WAIT` 且投诉仍升级；已解决拒绝回复；其他客服回复被拒；空内容 422 |
| 后端全量 | `uv run pytest -q`：**223 passed**；`ruff check app tests` 通过 |
| 前端检查 | `npm run typecheck`、Docker `npm run build` 通过 |
| 前端 E2E | `npm run test:e2e`：**29 passed**；人工工单用例覆盖接管 → 回复 → 解决，客服消息在后台与顾客聊天历史均可见，通知未读变为 2；另有常开聊天窗轮询用例验证客服回复自动出现 |
| 迁移 | 真实 PostgreSQL `alembic downgrade -1` → `upgrade head` 通过，`messages.role` 约束接受 `agent` |
| 任务评测 | `uv run python -m app.scripts.task_eval`：19/19 通过 |
| 审计链 | `uv run python -m app.scripts.verify_audit`：merchant=5 OK |
| 容器 | Compose 10 个服务均 Up，api/postgres/mailpit 为 healthy |

## 边界

- 没有 WebSocket、SSE 或推送通知；聊天窗打开时每 5 秒增量轮询一次，存在最多约 5 秒延迟，页面不可见时不做后台推送。
- 不做已读回执、正在输入或附件/图片；共享话术模板见 [quick-replies.md](quick-replies.md)，仅员工可见的工单备注见 [handoff-notes.md](handoff-notes.md)。
- 工单接管是单客服独占：其他客服（包括店主）不能直接回复，需要先改派。
- 回复不触发 AI 审核或承诺扫描；客服内容按人工输入原样发送，仍写审计但不做敏感词拦截。
- 解决后 AI 恢复自动答复，但没有「重新转人工」的独立状态机，新问题会复用或新建工单。
- 通知只覆盖站内与按偏好邮件，没有短信/IM 推送。