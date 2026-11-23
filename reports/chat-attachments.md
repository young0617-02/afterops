# 顾客聊天图片

- 验证时间：2026-11-05
- 范围：顾客聊天最多附 3 张图片；AI 明确不识别图片并保留转人工；人工工单可查看原图
- 迁移：`d9f1b3c5e7a0`（`messages.attachment_ids`）

## 行为

1. `POST /api/chat` 新增 `attachment_ids`（最多 3 个，去重后校验）；`text` 与图片至少提供一项，两者都空返回 422，纯图片消息允许 `text=""`。
2. 图片必须先经 `POST /api/store/attachments` 上传；只接受 PNG/JPEG/WebP 且不超过 5MB；附件必须属于当前顾客，跨顾客引用返回 422。
3. 用户消息的 `attachment_ids` 以 JSON 存在 `messages` 表；会话详情返回 `image_urls`（`/api/store/attachments/{id}`，仅本人可读），图片按消息顺序展示，会话摘要空文本显示「（图片）」。
4. 有文字时 AI 正常回答文字意图，并在回复末尾明确「无法识别图片内容」；纯图片时意图为 `ATTACHMENT`，不自动建单，回复要求补充文字或回复「转人工」。
5. 已转人工的会话继续收图：图片随消息进入人工队列，客服回复提示图片已转交；工单详情消息返回 `/api/admin/attachments/{id}`，后台会话线程展示缩略图并可打开原图。
6. 审计只记录 `attachment_count`，不记录图片内容、文件名或 URL；`chat_answered` 与 `chat_handoff_message` 均带该计数。
7. 聊天窗最多选择 3 张、可逐张移除、缩略图预览后发送；后台工单与顾客历史均显示已发图片。

## 验证结果

| 项 | 结果 |
|---|---|
| 后端测试 | `tests/test_chat_attachments.py`：文字+2 图落库与 `image_urls`、纯图片回复与空消息/超限 422、跨顾客附件拒绝、转人工后坐席可见并可读取原图 |
| 后端全量 | `uv run pytest -q`：**254 passed**；`ruff check app tests` 通过 |
| 前端检查 | `npm run typecheck`、Docker `npm run build` 通过 |
| 前端 E2E | `npm run test:e2e`：**33 passed**；新增「聊天发图 → 转人工 → 后台工单查看缩略图」 |
| 迁移 | 真实 PostgreSQL `upgrade head` → `downgrade -1` → `upgrade head` 通过 |
| 任务评测 | `uv run python -m app.scripts.task_eval`：19/19 通过 |
| 审计链 | `uv run python -m app.scripts.verify_audit`：merchant=5 OK |
| 容器 | api/web/worker 已重建；10 个服务 Up，api/postgres/mailpit 为 healthy |

## 边界

- 无图片识别/OCR/视觉模型：AI 不读取像素内容，只提示人工查看；不根据图片自动判责或建单。
- 只支持顾客发图；客服回复仍为纯文字，不能发送图片或附件。
- 未做压缩、缩略图服务端生成、EXIF 清洗与恶意文件扫描；仅按文件头校验图片类型和大小。
- 未做孤儿附件清理：已上传但未随消息发送的附件会保留；附件也未被消息删除联动清理。
- 图片链接依赖登录 Cookie（顾客本人 / 商家员工），不是签名 URL；没有过期时间。
- 备份流程覆盖附件目录，但没有按消息生命周期做归档或删除策略。
