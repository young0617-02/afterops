# 顾客退款记录与净支付

- 验证时间：2026-11-09
- 范围：顾客订单详情逐笔展示退款/补偿记录、退款原因、处理引用、累计退款与净支付
- 迁移：无（读取现有 `refunds` 与提案原因）

## 行为

1. `GET /api/store/orders/{order_no}` 新增 `refunds[]`：类型、金额、状态、原因、处理引用、创建/执行时间；仅订单归属顾客可读。
2. 退款原因从关联 `action_proposals.payload_json.reason` 读取；手工补偿、部分取消、质检扣减、退货退款与换货差价均可在同一列表查看。
3. 订单详情新增 `refunded_total_cents/display`（仅统计已执行 `refund`）与 `net_paid_cents/display = payable − 累计退款`；`compensation` 展示但不冲减净支付。
4. 顾客订单详情新增「退款记录」表，包含类型、金额、状态、原因、时间与处理引用，并提示到账时间以支付渠道为准。
5. 无退款时不展示退款表；全额退款时累计退款等于实付、净支付为 0。
6. 处理引用沿用 mock PSP 的 `MOCK-REFUND-*` / `MOCK-COMP-*`；不改变现有退款执行、审批与审计逻辑。

## 验证结果

| 项 | 结果 |
|---|---|
| 后端测试 | `tests/test_customer_refund_ledger.py`：无退款为空且净支付=实付；整单退款原因/引用/累计/净支付正确；部分取消退款金额与原因进入列表；补偿展示但不减少净支付 |
| 后端全量 | `.venv/Scripts/python.exe -m pytest -q`：**294 passed**；`ruff check app tests` 通过 |
| 前端检查 | `npm run typecheck`、Docker `npm run build` 通过 |
| 前端 E2E | `npx playwright test`：**38 passed**；部分取消用例增加「累计退款 30.00 元、净支付 50.00 元、原因与已执行状态」断言 |
| 迁移 | 无数据库迁移 |
| 任务评测 | `python -m app.scripts.task_eval`：19/19 通过 |
| 审计链 | `python -m app.scripts.verify_audit`：merchant=5 OK |
| 容器 | api/web/worker 已重建；10 个服务 Up，api/postgres/mailpit 为 healthy |

## 边界

- 退款状态来自内部 mock 账本，不查询支付渠道；到账时间不承诺。
- 列表不区分优惠券返还、运费退还等明细科目；仅展示账本退款金额与提案原因。
- 不提供退款凭证下载、电子回单、发票冲减或跨订单汇总页。
- 补偿与退款同表展示，但补偿不计入累计退款和净支付。
- 单订单退款记录未分页；极端多退款场景下一次性返回全部。
