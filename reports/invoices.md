# 发票申请与开票

- 验证时间：2026-11-05
- 范围：已支付订单申请个人/企业发票，店主在财务页开票或驳回
- 迁移：`fe0ea55b3627`（`invoice_requests`）

## 行为

1. 顾客在订单详情对已支付/部分退款订单申请发票；每单只能申请一次。
2. 发票类型个人/企业；企业必须填税号；抬头、税号、邮箱有长度校验。
3. 申请金额取申请时订单应付金额；状态 `requested → issued / rejected`。
4. 店主在「财务」页签查看发票队列，填写发票号开票或填写原因驳回；客服只读。
5. 开票/驳回通知顾客（站内 + 顾客邮件偏好），并写 `invoice_issued` / `invoice_rejected` 审计。
6. 顾客订单详情显示发票状态、发票号或驳回原因。

## API

| 方法 | 路径 | 权限 | 说明 |
|---|---|---|---|
| GET | `/api/store/invoices` | 顾客 | 我的发票 |
| POST | `/api/store/invoices` | 顾客 | `{order_no, invoice_type, title, tax_no, email}` |
| GET | `/api/admin/invoices?status=` | 店主/客服 | 发票队列 |
| POST | `/api/admin/invoices/{id}/issue` | 仅店主 | `{invoice_no}` |
| POST | `/api/admin/invoices/{id}/reject` | 仅店主 | `{reason}` |

## 验证结果

| 项 | 结果 |
|---|---|
| 后端测试 | `tests/test_invoices_api.py`：申请/金额/重复 409、未支付 409、企业缺税号 422、越权 404、开票/驳回、客服 403、审计 |
| 后端全量 | `uv run pytest -q`：**216 passed**；`ruff check app tests` 通过 |
| 前端检查 | `npm run typecheck`、Docker `npm run build` 通过 |
| 前端 E2E | `npm run test:e2e`：**28 passed**；顾客申请发票 → 店主在财务页开票 → 顾客看到「已开票」与发票号 |
| 任务评测 | `uv run python -m app.scripts.task_eval`：19/19 通过 |
| 实机数据 | PostgreSQL 发票 `#2`：`ORD-20261104-6D4A4DD8`、899.00 元、`issued`、`INV-E2E-1793818396211` |
| 审计链 | `uv run python -m app.scripts.verify_audit`：merchant=5 OK |

## 边界

- 只记录开票信息，不接税务系统、不开具真实 PDF/数电票，也不校验发票号合法性。
- 每单只能申请一次；不支持修改抬头、换开、红冲或作废。
- 金额取申请时订单应付金额；部分退款后不自动调整已开票金额，也不支持差额开票。
- 未支付订单不能申请；申请邮箱只展示记录，不自动发送 PDF。
- 无发票额度、抬头常用信息、批量开票或导出；财务页没有按日期筛选。
- 发票通知走顾客邮件偏好中的售后类别，没有独立订阅开关。
