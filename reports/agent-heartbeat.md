# 坐席心跳与在线自动分配

- 验证时间：2026-11-09
- 范围：后台管理员定时心跳判定在线；自动分配只选择心跳在线且接单中的客服；团队页展示在线/离线与最近心跳
- 迁移：`e7a9c1d3f5b8`（`users.last_seen_at`）

## 行为

1. 新增 `POST /api/admin/heartbeat`，任意已登录管理员可调用；写入 `last_seen_at` 并返回当前 `online`、接单状态与最近心跳时间。
2. 管理员登录、切换接单状态也会刷新 `last_seen_at`；心跳本身不写审计，避免 30 秒一条审计污染链。
3. 在线判定 = 账号启用 + `handoff_available=True` + 最近心跳在 `AGENT_ONLINE_WINDOW_SECONDS`（默认 90 秒）内。
4. 自动分配只选择在线的客服，再按当前处理中工单数最少、用户 ID 排序；无在线客服时工单保持 `open` 待接管，不会分配给离开或心跳过期的账号。
5. 团队列表 `GET /api/admin/staff` 返回 `handoff_available/last_seen_at/online`；团队页展示「在线 / 离线 / 已离开」徽章与最近心跳，并每 30 秒静默刷新。
6. 后台页面登录后立即发送心跳，并每 30 秒、标签页重新可见和窗口重新聚焦时补发；离开后台页面后心跳自然过期。
7. 自动分配开关说明更新为「心跳在线（90 秒内）且接单中、负载最少」；原有手动指派不受在线状态限制。

## 验证结果

| 项 | 结果 |
|---|---|
| 后端测试 | `tests/test_handoff_auto_assign.py`：心跳返回在线、团队列表在线字段、心跳过期转离线、离开状态离线；原有自动分配用例补充客服登录心跳，离开/在线分流仍通过 |
| 后端全量 | `.venv/Scripts/python.exe -m pytest -q`：**290 passed**；`ruff check app tests` 通过 |
| 前端检查 | `npm run typecheck`、Docker `npm run build` 通过 |
| 前端 E2E | `npx playwright test`：**38 passed**；团队页用例增加「当前店主在线、新建客服离线且暂无心跳」断言 |
| 迁移 | 真实 PostgreSQL `upgrade head` → `downgrade -1` → `upgrade head` 通过 |
| 任务评测 | `python -m app.scripts.task_eval`：19/19 通过 |
| 审计链 | `python -m app.scripts.verify_audit`：merchant=5 OK |
| 容器 | api/web/worker 已重建；10 个服务 Up，api/postgres/mailpit 为 healthy |

## 边界

- 在线状态仅在后台页面打开时维持；关闭后台后 90 秒内仍视为在线，之后自动离线，没有服务端主动踢下线。
- 没有 WebSocket/SSE 推送在线状态；团队页 30 秒轮询刷新，存在最长约 30 秒显示延迟。
- 单账号多设备不区分设备；任一在线标签页都会维持在线。
- 自动分配不考虑技能、班次、时区、最大并发与工单优先级，仅按在线与当前处理量。
- 心跳不记录审计，也没有在线时长统计、离开原因或排班。
