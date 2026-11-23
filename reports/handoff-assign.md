# 工单指派与改派

- 验证时间：2026-11-04
- 范围：店主把人工工单指派/改派给启用坐席，通知被指派人并写审计
- 接口：`POST /api/admin/handoffs/{id}/assign`（仅店主）

## 行为

1. 店主在工单卡选择启用坐席（owner/agent 均可）并点击「指派」；已接管工单显示「改派」。
2. 服务端校验工单未解决、被指派人属于当前商家且状态为 active；指派后工单进入 `claimed`。
3. 写 `handoff_assigned` 审计（工单、被指派人、操作人）；给被指派人写站内通知，启用邮件时写 outbox。
4. 被指派人可查看会话并标记解决；其他客服不能抢走已指派工单。
5. 客服仍可对未指派工单自助「接管」；店主指派会覆盖已有接管人。

## 验证结果

| 项 | 结果 |
|---|---|
| 后端测试 | `tests/test_handoffs_api.py`：店主指派给 agent、被指派人/邮件通知、审计、agent 不能指派（403）、被指派人可解决 |
| 后端全量 | `uv run pytest -q`：**164 passed**；`ruff` 通过 |
| 前端 E2E | `npm run test:e2e`：**19 passed**；店主选择 agent 指派并看到处理人 |
| 任务评测 | `uv run python -m app.scripts.task_eval`：19/19 通过 |
| 审计链 | `uv run python -m app.scripts.verify_audit`：OK |

## 边界

- 手动指派/改派仍保留；另有商家级「新工单自动分配」开关，按当前负载选择客服，见 [handoff-auto-assign.md](handoff-auto-assign.md)；自动分配会跳过手动标记离开的客服（见 [handoff-availability.md](handoff-availability.md)）；仍无实时在线心跳/严格轮询/技能路由。
- 指派不校验坐席当前工作量或在线情况；可能指派给离线账号。
- 被指派人停用后，工单仍显示原处理人；需店主改派或客服接管规则处理。
- 指派通知发给被指派人；不会额外通知原处理人。
- 指派不改变优先级/SLA 计时起点。
