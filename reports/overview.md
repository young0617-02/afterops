# 运营概览

> 后续更新：概览已支持 24 小时 / 7 天 / 30 天窗口与趋势 CSV 导出，见 [overview-range.md](overview-range.md)。本页指标口径与数字为当时快照。

- 验证时间：2026-11-03
- 入口：`/admin` → 运营概览（默认页签）
- 接口：`GET /api/admin/overview`，客服/店主可读

## 指标定义

| 指标 | 口径 |
|---|---|
| 待审批提案 | `action_proposals.status = pending` |
| 待接管工单 / 处理中 | `handoff_tickets.status = open / claimed` |
| 待履约订单 | 已支付且 `fulfillment_status` 为 `unfulfilled/allocated`，排除取消/退款 |
| 待处理退货 | `return_requests.status` 为 `requested/approved/in_transit` |
| 未读通知 | 当前登录客服账号的站内通知 |
| 支付笔数/金额 | 近 24 小时 `payments.paid_at`，金额为支付毛额（含后续退款） |
| 退款笔数/金额 | 近 24 小时已执行退款 |
| AI 回复 | 近 24 小时 `chat_answered` 审计事件数 |
| 新/执行/驳回提案 | 近 24 小时创建、执行、驳回 |
| 新/解决工单 | 近 24 小时创建、解决 |
| 审批中位耗时 | 近 24 小时已执行/驳回提案的 `approved_at - created_at` 中位数 |
| 审计链 | 整条商家链哈希自洽校验 |
| 近 7 天趋势 | 按 UTC 日期聚合支付笔数/金额、退款、AI 回复、提案、工单 |

## 验证结果

| 项 | 结果 |
|---|---|
| 后端测试 | `tests/test_overview_api.py`：鉴权、待办、支付/退款、提案/工单、AI 回复、审批中位耗时、待履约随发货变化、agent 可读 |
| 后端全量 | `uv run pytest -q`：**119 passed**；`ruff` 通过 |
| 前端 E2E | `npm run test:e2e`：**10 passed**；店主登录后概览可见，待审批/退货卡片、近 7 天趋势与审计链状态可见 |
| 任务评测 | `uv run python -m app.scripts.task_eval`：14/14 通过 |
| 浏览器实测 | msedge 1440×1000：待办 5 卡、近 24 小时 6 卡、近 7 天趋势表、审计状态条与页签跳转正常 |

## 边界

- 概览固定 24 小时滚动窗口 + 近 7 天日趋势（UTC）；无自定义日期范围、图表库、导出或同比环比。
- 支付为**毛额**，包含之后被退款的支付；退款单独统计，不能直接相减当作净收入。
- 只统计本商家；开发/评测数据会明显抬高读数，页面读数不代表生产经营状况。
- 审计链每次加载都做整链校验，事件量大时为 O(n)；未做缓存。
- 没有商品维度的销量/库存周转、没有 AI 自主解决率、成本或利润口径。
