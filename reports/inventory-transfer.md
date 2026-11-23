# 跨仓库存调拨

- 验证时间：2026-11-05
- 范围：同一商家内按商品在仓库之间调拨可售库存，双仓流水、审计与对账保持一致
- 依赖：库存流水 `inventory_movements`（迁移 `7036da747308`）

## 行为

1. 店主在「商品库存」某个仓库行点击「调拨」，填写调入仓库编号、数量和可选备注，提交 `POST /api/admin/products/{sku}/inventory/transfer`。
2. 服务先锁商品行，再按固定顺序读取并锁定调出/调入库存行；同一商品的并发库存写操作被商品行锁串行化。
3. 调出仓必须已存在且可售库存足够，否则分别返回 404 / 409；数量必须为正整数，调出与调入仓不能相同，否则 422。
4. 调入仓不存在时自动创建一条可售为 0 的库存行，仓库名称默认取仓库编号。
5. 调拨成功写两条流水：调出仓 `transfer_out`（负变动）与调入仓 `transfer_in`（正变动），备注一致；并写一条 `inventory_transferred` 审计事件，记录双仓前后数量、数量、操作人与备注。
6. 调出仓若跌破低库存阈值，按既有规则通知员工；前端成功后提示「SKU：调出仓 → 调入仓 调拨 N 件完成」，刷新商品、流水与对账。
7. 只允许店主操作，客服账号返回 403。

## 验证结果

| 项 | 结果 |
|---|---|
| 后端测试 | `tests/test_inventory_transfer.py`：初始 10 → 调拨 4 到新仓，响应前后数量正确、两仓结存 6/4、两条流水 -4/+4、审计载荷完整、对账差异 0；数量 0、同仓、库存不足、源仓不存在、客服越权分别被拒绝 |
| 后端全量 | `uv run pytest -q`：**220 passed**；`ruff check app tests` 通过 |
| 前端检查 | `npm run typecheck`、Docker `npm run build` 通过 |
| 前端 E2E | `npm run test:e2e`：**28 passed**；商品用例覆盖 6 件库存从 default 调拨 2 件到 east，两仓输入值 4/2、流水出现调拨调出/调入且备注一致、对账差异 0 |
| 并发探针 | `app/scripts/inventory_transfer_probe.py` 真实 PostgreSQL 两线程反向调拨：`quantities={'default': 50, 'east': 50}, movements=4, chain intact` |
| 任务评测 | `uv run python -m app.scripts.task_eval`：19/19 通过 |
| 审计链 | `uv run python -m app.scripts.verify_audit`：merchant=5 OK |
| 容器 | Compose 10 个服务均 Up，api/postgres/mailpit 为 healthy |

## 边界

- 只调拨可售库存 `qty_available`；调出仓的 `qty_reserved` 不随之迁移，已预留订单仍在原仓履约。
- 没有调拨单、在途状态、审批流或收货确认，提交即同时改变两仓库存。
- 两条流水没有独立 `transfer_id`，只能通过审计事件与备注关联；按流水无法直接成对查询一次调拨。
- 不按批次、库位、成本或序列号调拨，也不生成运输/损耗凭证。
- 目标仓库自动建行时没有独立仓库主数据，名称默认等于编号，之后可在「新增/更新仓库」里改名。
- 不支持跨商家调拨、调拨撤回或差异盘点后自动冲销。
