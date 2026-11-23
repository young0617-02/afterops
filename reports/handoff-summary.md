# 工单会话摘要

- 验证时间：2026-11-10
- 范围：人工工单自动生成结构化会话摘要（诉求、订单、客户最近反馈、已回复、建议、会话规模），客服可在后台查看并手动刷新；AI 跟进消息与客服回复会自动更新摘要
- 迁移：`e4f6a8c0d2b5`（`handoff_tickets.summary` / `summary_generated_at`）

## 行为

1. `HandoffTicket` 新增 `summary` 与 `summary_generated_at`；工单列表、详情与刷新接口均返回摘要。
2. 工单创建时生成摘要；活跃工单收到新的 AI 对话轮次或客服回复时自动刷新摘要；后台提供「刷新摘要」按钮手动重算。
3. 摘要为规则化结构化文本，不调用生成式模型：包括意图（中文标签）、从会话与工单字段提取的最多 3 个订单号、最近两条顾客消息（截断）、最新回复（截断）、按意图给出的处理建议、消息总数/队列/优先级。
4. 处理建议只描述核对与流程方向，不包含退款/赔付/到账承诺；摘要不替代工单内部备注、回复或解决说明。
5. 手动刷新写审计 `handoff_summary_refreshed`，记录工单号与当前消息数；自动刷新不额外写审计，避免每轮对话膨胀审计链。
6. 后台工单卡展示摘要生成时间与全文，客服/店主均可刷新；自动刷新保证客服打开工单时摘要与最近对话一致。
7. 摘要生成只读取最近 30 条消息做内容提取，消息总数单独统计；无消息时给出「无文字描述」与通用建议。

## 验证结果

| 项 | 结果 |
|---|---|
| 后端测试 | `tests/test_handoffs_api.py::test_handoff_summary_contains_context_and_refreshes`：工单摘要含订单号/诉求/建议/消息数，跟随后续消息更新，手动刷新写审计 |
| 后端全量 | `.venv/Scripts/python.exe -m pytest -q`：**318 passed**；`.venv/Scripts/ruff check app tests` 通过 |
| 前端检查 | `npm run typecheck` 通过；Docker `npm run build` 随 web 重建通过 |
| 前端 E2E | 工单用例增加摘要可见与「刷新摘要」提示；全量 **41 passed** |
| 迁移 | 真实 PostgreSQL `upgrade head` → `downgrade -1` → `upgrade head` 通过 |
| 任务评测 | `python -m app.scripts.task_eval`：19/19 通过 |
| 审计链 | `python -m app.scripts.verify_audit`：merchant=5 OK |
| 容器 | api/web/worker 已重建；10 个服务 Up，api/postgres/mailpit 为 healthy |

## 边界

- 摘要是规则模板，不是 LLM 生成；不做语义聚类、情绪判断或跨会话归并。
- 订单号靠正则从消息与工单最近字段提取，最多 3 个；不查询订单系统补全或校验订单归属。
- 只读取最近 30 条消息；更早内容不进入摘要，消息总数仍按全量统计。
- 不识别图片内容（聊天/工单图片只以文字或占位存在），不做 OCR。
- 处理建议按意图固定映射，不随订单状态或售后单据动态变化。
- 摘要无版本历史；被覆盖后只能通过审计看到刷新动作，不能回溯旧摘要全文。
- 客服内部备注不进入摘要，摘要也不自动写回顾客侧。
