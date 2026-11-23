# 聊天 SSE 实时推送

- 验证时间：2026-11-05
- 范围：顾客聊天窗实时接收客服/AI 新消息，断线回退轮询
- 迁移：无（沿用 `messages` 表）

## 行为

1. 新增 `GET /api/chat/conversations/{id}/stream`，返回 `text/event-stream`；仅会话所属顾客可读，未登录 401、越权 404。
2. 查询参数 `after_id` 与请求头 `Last-Event-ID` 取较大值作为起点；每条消息发送 SSE `id` 与 JSON `data`，字段与历史消息接口一致（含 `image_urls`）。
3. 服务端每秒检查新消息，默认最多保持 25 秒（`wait_seconds` 1–30，测试用 1 秒），超时发送注释后关闭；浏览器按 `retry: 3000` 自动重连。
4. 流建立时发送 `: connected`，空闲每 10 秒发送 `: ping`；响应头禁用缓存与代理缓冲。
5. 前端在会话存在后建立 EventSource；收到消息按 ID 去重追加，显示「实时」标识；连接失败时关闭实时模式，回退现有 5 秒轮询。
6. 发送消息仍走 `POST /api/chat`，不受 SSE 影响；AI 与客服回复均通过同一会话流推送。
7. 附带修复财务发票列表的过期响应竞态：筛选切换时只接受最后一次请求结果，避免旧筛选结果覆盖新列表。

## 验证结果

| 项 | 结果 |
|---|---|
| 后端测试 | `tests/test_chat_stream.py`：历史回放、`after_id` 无增量、`Last-Event-ID` 续传、事件 JSON/内容类型、未登录 401、越权 404、非法参数 422 |
| 后端全量 | `uv run pytest -q`：**266 passed**；`ruff check app tests` 通过 |
| 前端检查 | `npm run typecheck`、Docker `npm run build` 通过 |
| 前端 E2E | `npm run test:e2e`：**33 passed**；实时聊天用例断言「实时」标识出现，客服回复在 4 秒内到达；全量复跑通过 |
| 迁移 | 本片无迁移；当前 head `c4e6f8a0d2b5` |
| 任务评测 | `uv run python -m app.scripts.task_eval`：19/19 通过 |
| 审计链 | `uv run python -m app.scripts.verify_audit`：merchant=5 OK |
| 容器 | api/web/worker 已重建；10 个服务 Up，api/postgres/mailpit 为 healthy |

## 边界

- HTTP SSE 单向推送，不是 WebSocket；发送消息仍需普通 POST，没有客户端双向通道。
- 服务端每连接每秒轮询数据库，不是 Postgres `LISTEN/NOTIFY` 或消息总线；大量并发连接会放大查询数，没有连接注册表/广播层。
- 流默认 25 秒后关闭并自动重连，重连间隙可能短暂回退轮询；没有断线期间的服务端缓冲。
- 仅顾客聊天窗接入 SSE；后台工单队列、通知铃铛仍为手动刷新/轮询，没有客服端实时队列。
- 无已读回执、正在输入、在线状态或消息编辑/撤回。
- 未做生产代理（Caddy/Nginx）SSE 超时与压缩配置验证，也未做并发流压测。
