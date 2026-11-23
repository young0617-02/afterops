# 未发货商品部分取消

- 验证时间：2026-11-09
- 范围：顾客按件申请取消尚未分配包裹的商品，店主审批后按商品折扣分摊退款、回补库存；剩余商品继续履约
- 迁移：`b4d6f8a0c2e4`（`order_items.cancelled_qty`）

## 行为

1. `POST /api/store/orders/{order_no}/cancel-request` 新增可选 `items: [{sku, qty}]`；不传 `items` 仍走原整单取消，兼容既有调用。
2. 可取消数量 = 订单数量 − 已取消数量 − 已分配包裹数量。已被包裹占用的数量、已发货数量和已取消数量都不能再次取消；无运单但已建包裹同样视为已分配。
3. 申请金额按商品小计减订单折扣分摊计算：`selected_subtotal − floor(discount × selected_subtotal / item_total)`，并以订单剩余可退金额封顶；若本次取消后全部明细都已取消且没有包裹，则按剩余应付全额退款（含运费）。折扣分摊的舍入差由剩余可退上限兜底，累计退款不超过实付。
4. 申请期间写入 `cancel_requested_at`，禁止创建新包裹与其它待审售后；店主驳回会解除冻结，审批通过会清理冻结。
5. 审批时重新校验可取消数量，随后逐件累加 `cancelled_qty`、按原仓回补库存、扣减/删除对应 committed 预留，并写 `order_partially_cancelled` 审计（商品、数量、回补行、是否全部取消）。
6. 部分取消后订单继续履约：`order.status` 保持 paid/fulfilling/shipped，`payment_status` 变为 partially_refunded；若所有明细都已取消，则订单与支付进入 refunded。已发货包裹可继续签收，全部包裹签收且已发+已取消数量覆盖全部明细后订单才进入 delivered。
7. 后续创建包裹只包含未取消且未分配的剩余数量；后台履约卡与订单详情展示已取消数量，顾客订单详情展示已取消列与按件取消表单。
8. 顾客取消申请提案展示申请原因与逐项取消商品，发起人标为「顾客申请」；后台仍须店主批准后执行。
9. 退货资格、退货数量上限、维修资格与商品评价均扣除/拦截已取消商品，避免对未收到商品发起售后。

## 验证结果

| 项 | 结果 |
|---|---|
| 后端测试 | `tests/test_partial_cancellation.py`：部分取消退款 199.00、库存回补与预留删除、剩余商品只发 1 件并完成订单；已分配数量不可取消、待审期间禁止发货、全部取消进入 refunded；折扣按比例分摊后两次取消累计等于实付；已取消商品退货 422、维修资格排除、维修 409、评价 409 |
| 后端全量 | `.venv/Scripts/python.exe -m pytest -q`：**286 passed**；`ruff check app tests` 通过 |
| 前端检查 | `npm run typecheck`、Docker `npm run build` 通过 |
| 前端 E2E | `npx playwright test`：**38 passed**；新增「顾客取消未发货商品 → 后台看到取消项并批准 → 顾客看到已取消数量 → 店主只发剩余商品 → 签收完成」 |
| 迁移 | 真实 PostgreSQL `upgrade head` → `downgrade -1` → `upgrade head` 通过 |
| 任务评测 | `python -m app.scripts.task_eval`：19/19 通过 |
| 审计链 | `python -m app.scripts.verify_audit`：merchant=5 OK |
| 容器 | api/web/worker 已重建；10 个服务 Up，api/postgres/mailpit 为 healthy |

## 边界

- 只能取消尚未分配包裹的数量；商品一旦进入包裹（即使包裹未揽收）就不能再从该包裹中取消或改数量。
- 不支持部分取消手续费、运费重算、按包裹取消、跨订单合并取消或取消后恢复；取消后只能重新下单。
- 退款仍走 mock PSP 账本；不接真实支付渠道，不承诺到账时间。
- 优惠券不退券、不恢复领取次数/使用次数；折扣按商品小计比例分摊，运费只在全部商品取消时退还。
- 订单明细保留原始数量，用 `cancelled_qty` 表达取消部分；发票不自动冲减或重开。
- 部分取消不发送独立的顾客邮件模板，沿用“退款已执行”站内通知/邮件偏好。
- 已取消商品不能退货、维修或评价；已发货部分仍按既有售后流程处理。
