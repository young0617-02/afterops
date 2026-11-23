# 通知实时推送（SSE）

- 验证时间：2026-11-10
- 范围：顾客与后台通知铃铛新增 SSE 流，新通知在 1 秒内推送到页面并更新未读数；保留 30 秒轮询兜底
- 迁移：无（只读现有 `notifications` 表）

## 行为

1. 新增顾客 `GET /api/store/notifications/stream` 与后台 `GET /api/admin/notifications/stream`；按 `after_id` 或 `Last-Event-ID` 从指定 ID 之后推送。
2. 流只读取当前登录顾客 / 后台用户的通知，按商家、收件类型与收件 ID 过滤；未登录后台访问返回 401。
3. 每个事件包含通知 ID、类型、标题、正文、跳转链接、已读时间与创建时间；事件带 `id:` 供 EventSource 断线续传。
4. 服务端每秒查询新通知，最多每次 50 条；空闲 10 秒发送 `: ping` 心跳；流最长 25 秒后结束，客户端自动重连。
5. 前端铃铛首次加载完成后连接 SSE；收到事件后将通知插入列表并递增未读数；面板打开时显示「实时」徽章。
6. 保留原 30 秒轮询作为兜底；SSE 断开或 401 时轮询仍可更新，重新加载成功后自动恢复 SSE。
7. 已读操作仍走原接口；SSE 不会重复推送已推送 ID，列表按通知 ID 去重并最多保留 10 条。

## 验证结果

| 项 | 结果 |
|---|---|
| 后端测试 | `tests/test_notification_stream.py`：顾客流重放退货通过通知、按 Last-Event-ID 续传、其他顾客为空；后台流鉴权、工单通知与续传 |
| 后端全量 | `.venv/Scripts/python.exe -m pytest -q`：**328 passed**；`.venv/Scripts/ruff check app tests` 通过 |
| 前端检查 | `npm run typecheck` 通过；Docker `npm run build` 随 web 重建通过 |
| 前端 E2E | 客服回复用例增加「顾客铃铛未读数实时变为 1 且出现客服回复通知」断言；全量 **42 passed** |
| 迁移 | 无迁移；服务重建后 `/health` 200 |
| 任务评测 | `python -m app.scripts.task_eval`：19/19 通过 |
| 审计链 | `python -m app.scripts.verify_audit`：merchant=5 OK |
| 容器 | api/web/worker 已重建；10 个服务 Up，api/postgres/mailpit 为 healthy |

## 边界

- 服务端采用每秒数据库轮询，不是 PostgreSQL LISTEN/NOTIFY 或消息队列；连接数增多时查询压力线性增长。
- 每条流最长 25 秒，依赖客户端 EventSource 自动重连；重连间隔由浏览器控制，不做服务端退避。
- 仅推送建立连接后的新通知；超过 25 秒的历史通知由首次列表加载补齐，不做完整回放分页。
- 无事件类型过滤、通知分组、桌面推送或邮件联动；SSE 只更新站内铃铛。
- 未读数是前端增量累加，极端并发或已读竞态下可能与服务端计数短暂不一致，打开铃铛重新加载会校正。
- 多 API 副本均直接读数据库，可同时推送；没有跨副本广播或连接数监控。
