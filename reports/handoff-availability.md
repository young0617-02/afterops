# 坐席接单状态

- 验证时间：2026-11-05
- 范围：店主/客服手动切换「接单中 / 离开」，自动分配只考虑接单中的客服
- 迁移：`c8e0f2a4b6d8`（`users.handoff_available`）

## 行为

1. 员工账号新增 `handoff_available`，默认接单中；存量账号迁移后默认接单中。
2. 后台顶部提供「接单中 / 已离开」切换按钮，店主与客服都可切换自己的状态；切换即时保存并写 `staff_availability_changed` 审计（含账号、状态、操作人）。
3. `GET /api/admin/me` 返回当前账号可用状态；`PATCH /api/admin/me/availability` 只能修改自己，body `{available: bool}`。
4. 工单自动分配只从「启用 + `role=agent` + 接单中」的账号中选择当前负载最少者；没有接单中的客服时新工单保持待接管。
5. 手动接管/店主指派不受接单状态限制；已分配工单不会因切换离开而自动改派。
6. 接单状态持久化在数据库，跨会话/刷新保留；没有自动休眠或心跳检测。

## 验证结果

| 项 | 结果 |
|---|---|
| 后端测试 | `tests/test_handoff_auto_assign.py`：一名客服离开后自动分配只选另一名接单中客服；全部离开时新工单保持待接管；客服切换自己状态后 `GET /me` 反映并写审计；客服改分配设置仍 403 |
| 后端全量 | `uv run pytest -q`：**250 passed**；`ruff check app tests` 通过 |
| 前端检查 | `npm run typecheck`、Docker `npm run build` 通过 |
| 前端 E2E | `npm run test:e2e`：**32 passed**；团队页用例切换接单状态并恢复原状态 |
| 迁移 | 真实 PostgreSQL `upgrade head` → `downgrade -1` → `upgrade head` 通过 |
| 任务评测 | `uv run python -m app.scripts.task_eval`：19/19 通过 |
| 审计链 | `uv run python -m app.scripts.verify_audit`：merchant=5 OK |
| 容器 | api/web 已重建；10 个服务 Up，api/postgres/mailpit 为 healthy |

## 边界

- 是手动开关，不是真实在线状态/心跳；浏览器关闭不会自动离开。
- 只影响新工单自动分配；手动接管、被指派与已有工单不受限制，也没有自动改派。
- 没有按队列/技能/优先级分别设置可用性，也没有排班或离开原因。
- 店主不能替其他客服切换状态；所有员工只能改自己。
- 没有离开期间的通知/邮件或队列告警；工单仍靠接管 SLA 提醒。
- 切换记录只写审计，没有状态历史页面或在线时长统计。
