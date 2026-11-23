# 逐件换货与多商品替换

- 验证时间：2026-11-05
- 范围：部分商品换货、逐件指定同款/更低价换货 SKU、差价汇总
- 迁移：`e6f8a0c2d4b7`（`return_items.exchange_sku / exchange_unit_price_cents`）

## 行为

1. 退货明细新增换货 SKU 与换货单价快照；`resolution=exchange` 时每条明细可独立指定目标商品，留空表示同款。
2. 取消「换货仅支持整单退回」：多商品订单可只选择部分商品换货，也支持同款与跨 SKU 混合。
3. 兼容旧单商品接口：请求级 `exchange_sku` 仍可用，但仅在只选一件商品时允许；多商品必须按明细指定，否则返回 409。
4. 目标 SKU 必须存在且在售，价格不得高于原商品单价；高价换货仍拒绝。退款单携带换货字段返回 422。
5. 确认收货时按退货明细生成替换订单：同一目标 SKU 合并为一条替换明细并合并占用库存；原退回件仍按质检处置回补/报损。
6. 差价按每条明细 `(原单价 - 目标单价) × 数量` 汇总，生成**一个**退款提案等待店主审批；同款换货差价为 0。
7. 顾客售后页逐件显示换货目标选择；后台退货明细与顾客进度显示「→ 换 SKU」；旧数据仍按请求级换货字段兼容。
8. `return_received` 审计新增 `exchange_items` 与差价；`exchange_order_created` 审计带替换明细与差价。

## 验证结果

| 项 | 结果 |
|---|---|
| 后端测试 | `tests/test_partial_returns.py`：部分退一件并跨 SKU 换货、差价 700 元、替换订单/库存/退款提案；同款+跨 SKU 混合换货合并目标、差价汇总；高价换货 409、多商品使用请求级换货 409、退款携带换货 422；旧单商品换货与整单同款换货回归通过 |
| 后端全量 | `uv run pytest -q`：**268 passed**；`ruff check app tests` 通过 |
| 前端检查 | `npm run typecheck`、Docker `npm run build` 通过 |
| 前端 E2E | `npm run test:e2e`：**35 passed**；新增「多商品订单只换一件、逐件选择目标 SKU → 生成替换订单与差价退款」 |
| 迁移 | 真实 PostgreSQL `upgrade head` → `downgrade -1` → `upgrade head` 通过 |
| 任务评测 | `uv run python -m app.scripts.task_eval`：19/19 通过 |
| 审计链 | `uv run python -m app.scripts.verify_audit`：merchant=5 OK |
| 容器 | api/web/worker 已重建；10 个服务 Up，api/postgres/mailpit 为 healthy |

## 边界

- 只支持同价或更低价换货，不支持加价；差价只退不补收。
- 替换订单会按目标 SKU 合并明细，不保留「原明细 → 替换明细」的逐行映射；追溯需看退货明细与审计。
- 替换订单运费为 0，不重算配送方式/运费；也不支持跨订单换货或换不同数量（数量跟随退回件）。
- 旧请求级 `exchange_sku` 仅单商品可用；多商品必须使用明细级字段。
- 目标 SKU 的可换范围仅按价格与原单价判断，不校验类目/品牌/库存直到确认收货占用库存。
- 换货差价退款仍需人工审批；审批执行前替换订单已创建并占用库存。
