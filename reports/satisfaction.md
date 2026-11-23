# 售后满意度（CSAT）

- 验证时间：2026-11-04
- 范围：已解决人工工单的顾客评分与评论；坐席绩效与后台最近评价
- 迁移：`9c13ce47fd0d`（`satisfaction_ratings`）

## 行为

1. 顾客可在「售后进度 → 人工工单」对已解决工单提交 1–5 星评分和可选评论（最多 500 字）。
2. 每张工单只能评价一次；重复提交返回 409；工单必须属于当前顾客且状态为已解决。
3. 评价写入 `satisfaction_ratings`，并写审计事件 `handoff_rated`（工单、分数、评论、解决人）。
4. 顾客工单列表返回 `rating_score/rating_comment`，提交后卡片显示「已评价 X 星」。
5. 坐席绩效接口按评价发生时间窗口统计：总评价数、平均分、每个解决人的评价数与平均分；最近 10 条评价含工单号、顾客手机号、坐席邮箱与评论。
6. 团队页绩效表增加「满意度」列，下方展示最近评价。
7. 评价归属按工单 `resolved_by` 计算；不是评价时重新分配。

## API

- `POST /api/store/handoffs/{ticket_id}/rating`，body `{ "score": 1-5, "comment": "可选" }`。
- `GET /api/store/handoffs` 新增 `rating_score`、`rating_comment`。
- `GET /api/admin/agent-performance` 新增顶层 `csat_count/csat_average/recent_ratings` 和每行 `csat_count/csat_average`。

## 验证结果

| 项 | 结果 |
|---|---|
| 后端测试 | `tests/test_satisfaction_api.py`：解决工单评分、列表回显、重复 409、未解决 409、越权 404、分数/评论长度 422、审计、绩效 CSAT 与最近评价 |
| 后端全量 | `uv run pytest -q`：**193 passed**；`ruff check app tests` 通过 |
| 前端检查 | `npm run typecheck`、Docker `npm run build` 通过 |
| 前端 E2E | `npm run test:e2e`：**24 passed**；客服解决工单后顾客提交 5 星「客服处理很快」，团队页出现绩效列与最近评价 |
| 任务评测 | `uv run python -m app.scripts.task_eval`：19/19 通过 |
| 实机数据 | PostgreSQL `satisfaction_ratings`：工单 167/168，5 星「客服处理很快」，归属 `agent@afterops.local` |
| 审计链 | `uv run python -m app.scripts.verify_audit`：merchant=5 OK |

## 边界

- 只支持人工工单评价；退款、退货、订单完成等流程没有 CSAT。
- 评价一次性，不支持修改、删除或商家回复；恶意/辱骂内容没有审核流程。
- 评分只归属工单解决人；没有按团队、队列或工单优先级汇总的 CSAT 看板。
- 没有评价提醒/邮件；顾客需要主动进入售后进度页。
- 评价窗口按评价创建时间计入，不是工单解决时间；滚动小时窗口，不是自然日/月。
- 没有 CSAT 目标、趋势图、导出或低分告警；最近评价仅展示 10 条。
