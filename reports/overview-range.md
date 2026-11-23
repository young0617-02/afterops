# 运营概览：时间范围与趋势导出

- 验证时间：2026-11-04
- 范围：概览统计窗口、趋势区间、趋势 CSV 导出
- 接口：`GET /api/admin/overview?hours=1..720`（默认 24）
- 趋势：`GET /api/admin/overview/trend?days=1..30`（默认 7）

## 变更

1. 概览统计从固定 24 小时改为 `hours` 查询参数，支持 24 小时 / 7 天 / 30 天。
2. 字段统一去掉 `_24h` 后缀：`paid_orders`、`refunds`、`proposals_created`、`proposals_executed`、`proposals_rejected`、`handoffs_created`、`handoffs_resolved`、`ai_chats`；`window_hours` 返回实际窗口。
3. 前端概览页新增时间范围选择；区间为 7 天时趋势显示 7 天，30 天时趋势显示 30 天。
4. 趋势表新增「导出 CSV」，列包含日期/支付笔数与金额/退款笔数与金额/AI 回复/提案/工单；CSV 带 UTF-8 BOM，Excel 直接打开中文不乱码。
5. 原有待办卡片与审计链状态不变。

## 验证结果

| 项 | 结果 |
|---|---|
| 后端测试 | `tests/test_overview_api.py`：默认 24 小时、`hours=168` 返回 168、`hours=0` 422、字段口径与趋势回归 |
| 后端全量 | `uv run pytest -q`：**150 passed**；`ruff` 通过 |
| 前端 E2E | `npm run test:e2e`：**15 passed**；概览切换到近 7 天/近 30 天，趋势标题与导出按钮可见 |
| 任务评测 | `uv run python -m app.scripts.task_eval`：16/16 通过 |
| 审计链 | `uv run python -m app.scripts.verify_audit`：OK |

## 边界

- 是滚动窗口（从当前时刻往前 N 小时/天），不是自然日/自然月账期。
- 支付金额为支付毛额，含后续退款，不等于净收入；没有利润、成本、税费口径。
- 趋势按 UTC 日期分桶；前端按本地日期展示，跨时区边界可能与分桶日不完全一致。
- CSV 在浏览器端生成，不落服务端、不记录导出审计；无定时报表/邮件推送。
- 开发/评测数据会抬高读数，不能当作经营结论。
