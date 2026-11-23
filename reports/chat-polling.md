# 顾客聊天增量轮询

- 验证时间：2026-11-05
- 范围：聊天窗打开期间自动拉取客服/系统新消息，不用手动刷新
- 依赖：`GET /api/chat/conversations/{id}` 新增 `after_id` 游标

## 行为

1. `GET /api/chat/conversations/{id}?after_id=N` 只返回 `id > N` 的消息，仍校验会话归属；不传 `after_id` 时返回完整历史。
2. 聊天窗每次打开加载完整历史并记录最大消息 ID；发送消息成功后重新同步完整会话，避免本地乐观消息与轮询结果重复。
3. 聊天窗保持打开且没有发送中的请求时，每 5 秒用最大 ID 增量拉取一次；只追加新消息，失败保持当前记录不变。
4. 客服回复以 `agent` 角色返回，界面显示「客服」作者标识与独立气泡；顾客自己的消息、AI 回复与客服回复顺序按消息 ID 排列。
5. 关闭聊天窗停止轮询；重新打开重新加载完整历史。

## 验证结果

| 项 | 结果 |
|---|---|
| 后端测试 | `tests/test_handoff_replies.py`：用客服消息 ID 作为 `after_id`，增量接口只返回后续 `agent` 消息；归属校验沿用会话接口 |
| 后端全量 | `uv run pytest -q`：**223 passed**；`ruff check app tests` 通过 |
| 前端检查 | `npm run typecheck`、Docker `npm run build` 通过 |
| 前端 E2E | `npm run test:e2e`：**29 passed**；常开聊天窗用例通过后台 API 写入客服回复，5 秒轮询后自动出现且显示「客服」标识 |
| 任务评测 | `uv run python -m app.scripts.task_eval`：19/19 通过 |
| 审计链 | `uv run python -m app.scripts.verify_audit`：merchant=5 OK |

## 边界

- 轮询间隔固定 5 秒，消息最多延迟约一个周期；不是 WebSocket/SSE 实时推送。
- 只在聊天窗打开时轮询；页面关闭或组件卸载后停止，不发送浏览器后台通知。
- 没有未读标记、声音提醒或会话列表实时刷新；客服回复的站内通知仍由铃铛 30 秒轮询。
- 每次轮询都发一次 HTTP 请求；会话很多时没有合并、退避或 ETag。
- 发送失败/轮询失败静默保留当前记录，不做重试队列；用户可重新打开聊天窗重新同步。
