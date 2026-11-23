# 后台财务对账

- 验证时间：2026-11-04
- 范围：支付/退款/补偿汇总、待审资金提案、账本异常、最近流水与 CSV
- 入口：`/admin` → 财务

## 接口与口径

- `GET /api/admin/finance?days=1..720`（默认 30），客服/店主可读。
- 支付：窗口内 `payments.paid_at`，状态为 paid/partially_refunded/refunded，金额为支付毛额。
- 退款：窗口内已执行 `refunds.kind=refund`；补偿单列 `kind=compensation`。
- 净额 = 支付毛额 − 退款；补偿不冲减净额，单独展示。
- 待审资金提案：pending 的 refund/compensation 提案数量与金额合计。
- 异常检查：
  - 累计退款超过订单应付；
  - 订单标记已支付/部分退款/已退款，但没有已支付流水；
  - 订单显示已退款/部分退款，但没有退款流水。
- 最近流水：支付与退款/补偿按时间倒序，最多 30 条；前端可导出 CSV。

## 验证结果

| 项 | 结果 |
|---|---|
| 后端测试 | `tests/test_admin_finance_api.py`：未登录 401、支付/退款/净额、待审提案、流水类型、agent 可读、超额退款异常 |
| 后端全量 | `uv run pytest -q`：**163 passed**；`ruff` 通过 |
| 前端 E2E | `npm run test:e2e`：**18 passed**；财务页签与汇总/待审/导出控件可见 |
| 任务评测 | `uv run python -m app.scripts.task_eval`：19/19 通过 |
| 审计链 | `uv run python -m app.scripts.verify_audit`：OK |

## 边界

- 只对账内部订单/支付/退款账本，不接真实 PSP 结算文件；mock PSP 不能证明真实资金。
- 异常检查是有限规则集，不覆盖重复支付、汇率、手续费、税务或跨期调整。
- 支付/退款按 UTC 时间窗口统计；退款为执行时间，不是发起时间。
- 净额为管理口径，不是会计收入/利润；补偿单独展示。
- 流水最多 30 条，CSV 在浏览器生成；无定时报表、财务导出审批或审计留痕。
