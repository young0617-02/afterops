# 已支付未发货订单取消申请

- 验证时间：2026-11-05
- 范围：顾客对已支付未发货订单申请取消、审批前禁止发货、批准退款并回补库存
- 依赖：订单新增 `cancel_requested_at`（迁移 `a8b0d2f4e6c9`）；复用退款提案与库存流水

## 行为

1. 顾客在「我的订单」对 `paid + unfulfilled` 且无运单的订单点击「申请取消」，调用 `POST /api/store/orders/{order_no}/cancel-request`。
2. 服务按订单行锁校验归属与状态：仅已支付、未履约、无运单、无待审售后、无已有取消申请；否则返回 409。
3. 创建一笔全额退款提案，`payload_json` 标记 `cancel_request=true`；订单写入 `cancel_requested_at`，通知员工并写 `order_cancel_requested` 审计。
4. 存在取消申请时，后台创建运单返回 409「订单有取消申请待审批，暂不能发货」。
5. 店主驳回提案后清除 `cancel_requested_at`，订单恢复可发货；顾客仍可重新申请。
6. 店主批准后按现有退款审批执行退款，订单与支付状态变为已退款；同时把该订单 `committed` 库存预留按原仓库回补可售数量，预留置为 `released`，写 `order_cancelled` 库存流水与 `order_cancel_restocked` 审计（含回补行）。
7. 顾客订单页在申请期间显示「取消申请待审核」；审批完成后按现有订单状态展示。
8. 已支付订单取消仍走人工审批，AI/顾客不能直接退款。

## 验证结果

| 项 | 结果 |
|---|---|
| 后端测试 | `tests/test_order_cancel_request.py`：申请生成全额提案并标记订单、重复申请 409、发货被拦截、驳回后解除拦截并可发货、员工通知与审计顺序；批准后退款状态、库存从 9 回补到 10、预留 released、`order_cancelled` 流水与回补审计；未支付/已发货订单拒绝 |
| 后端全量 | `uv run pytest -q`：**232 passed**；`ruff check app tests` 通过 |
| 前端检查 | `npm run typecheck`、Docker `npm run build` 通过 |
| 前端 E2E | `npm run test:e2e`：**31 passed**；顾客申请取消 → 订单显示待审核 → 后台发货被拒 → 驳回 → 再次发货成功 |
| 迁移 | 真实 PostgreSQL `upgrade head` → `downgrade -1` → `upgrade head` 通过 |
| 任务评测 | `uv run python -m app.scripts.task_eval`：19/19 通过 |
| 审计链 | `uv run python -m app.scripts.verify_audit`：merchant=5 OK |
| 容器 | api/web 已重建；Compose 10 个服务均 Up，api/postgres/mailpit 为 healthy |

## 边界

- 只支持整单全额取消；不支持部分商品取消、改数量或换货后取消。
- 只有 `paid + unfulfilled + 无运单 + 无待审售后` 可申请；已发货/已签收订单走退货或退款流程。
- 申请不会自动通过，也没有取消手续费、违约金或运费扣除；批准后按订单应付金额全额退款。
- 优惠券次数不会因已支付取消而释放（与现有已支付退款规则一致）。
- 回补依赖订单的 `committed` 库存预留；缺失预留时回退到订单商品的首个库存行，可能不是原发货仓。
- 取消申请本身没有超时自动撤销，仍受待审提案 SLA 提醒；一个订单同时只允许一笔待审售后。
- 没有通知邮件模板区分「取消申请」；员工收到站内通知与提案通知，顾客收到提案结果通知。
