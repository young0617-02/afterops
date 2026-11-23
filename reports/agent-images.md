# 坐席回复图片

- 验证时间：2026-11-05
- 范围：客服在人工工单回复中附最多 3 张图片；顾客聊天窗直接查看；员工附件按工单顾客归属
- 迁移：`e0a2c4f6b8d1`（`attachments.uploaded_by_user_id`）

## 行为

1. 新增 `POST /api/admin/handoffs/{ticket_id}/attachments`，仅店主/客服可用，图片仍限 PNG/JPEG/WebP、≤5MB；工单不存在 404，已解决 409。
2. 员工附件记录 `uploaded_by_user_id`，`customer_id` 固定为工单顾客；上传写 `handoff_attachment_uploaded` 审计（工单、附件 ID、类型、大小、操作人），不记录文件名。
3. `POST /api/admin/handoffs/{ticket_id}/messages` 新增 `attachment_ids`（最多 3 张），`content` 与图片至少一项；两者都空返回 422，纯图片回复允许空文字。
4. 回复只接受「员工上传 + 属于该工单顾客」的附件；顾客自己上传的图片、其他工单顾客的附件均返回 422。接管权限沿用原逻辑，其他客服不能回复。
5. 客服消息的 `attachment_ids` 进入原会话；顾客会话详情返回 `/api/store/attachments/{id}`，后台工单返回 `/api/admin/attachments/{id}`，两端均展示缩略图并可打开原图。
6. 纯图片回复的通知与工单列表摘要显示 `[图片×N]`；`handoff_agent_replied` 审计记录文字长度与 `attachment_count`。
7. 后台回复区支持选图、预览、移除、纯图片发送；发送成功后刷新工单线程。

## 验证结果

| 项 | 结果 |
|---|---|
| 后端测试 | `tests/test_handoff_attachments.py`：文字+2 图与纯图片回复、顾客读图、员工上传人落库、上传/回复审计与哈希链；跨工单附件、顾客图片、空回复、超限 422，缺失工单 404，已解决工单上传 409 |
| 后端全量 | `uv run pytest -q`：**256 passed**；`ruff check app tests` 通过 |
| 前端检查 | `npm run typecheck`、Docker `npm run build` 通过 |
| 前端 E2E | `npm run test:e2e`：**33 passed**；聊天图片用例扩展为「顾客发图 → 转人工 → 客服回图 → 顾客查看」双向链路 |
| 迁移 | 真实 PostgreSQL `upgrade head` → `downgrade -1` → `upgrade head` 通过 |
| 任务评测 | `uv run python -m app.scripts.task_eval`：19/19 通过 |
| 审计链 | `uv run python -m app.scripts.verify_audit`：merchant=5 OK |
| 容器 | api/web/worker 已重建；10 个服务 Up，api/postgres/mailpit 为 healthy |

## 边界

- 无 OCR/视觉识别；图片只作为人工会话材料，不参与自动判责、意图或提案。
- 只支持图片，不支持 PDF、视频或任意文件；未做压缩、服务端缩略图、EXIF 清洗与病毒扫描。
- 客服不能转发顾客上传的图片，也不能把其他工单/顾客的附件带入回复；只允许本工单员工上传图。
- 任一员工都可向未接管的工单预先上传图片，但回复仍要求由当前接管人发送；已解决工单不可上传。
- 图片链接依赖登录 Cookie，不是签名 URL；员工上传但未发送的图片按顾客归属可被该顾客按 ID 读取。
- 未做孤儿附件清理与消息删除联动；附件目录纳入备份，但没有生命周期归档策略。
