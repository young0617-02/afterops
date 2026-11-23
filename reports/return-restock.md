# 退货收货回补库存

- 验证时间：2026-11-04
- 范围：退货确认收货时按原订单发货仓回补可售库存，退款/换货均生效
- 相关：`backend/app/services/returns.py`、`backend/app/api/returns.py`、`frontend/components/AdminReturns.tsx`

## 行为

1. 店主在退货卡片确认收货时可勾选「按原发货仓回补库存」，默认勾选。
2. 后端按原订单 `inventory_reservations` 找到发货仓，对每个订单商品执行 `qty_available += qty`。
3. 退款与换货都走同一回补逻辑：同款换货先回补退回件，再由替换订单占用一件，净库存与购买后一致。
4. 店主取消勾选时不改库存，适用于残次/不可二次销售商品。
5. `return_received` 审计 payload 增加 `restock` 与 `restocked_items`（SKU、仓库、数量）；接口返回回补明细，后台提示「回补库存 N 件」。
6. 回补与收货、退款提案/替换订单在同一事务内提交，任一步失败整体回滚。
7. 找不到原预留记录时回退到该商品任一库存行；找不到库存行则跳过该商品，不凭空创建仓库。

## API

`POST /api/admin/returns/{id}/receive` 可选请求体：

```json
{ "restock": true }
```

不传时默认 `true`，兼容既有调用。响应在退货单字段外增加：

```json
{ "restocked_items": [{ "sku": "SKU-NCM-001", "warehouse_id": "华东", "qty": 1 }] }
```

## 验证结果

| 项 | 结果 |
|---|---|
| 后端测试 | `tests/test_return_restock.py`：退款回补原仓、跳过回补、换货回补后再占用；审计 payload；`tests/test_returns_api.py` 同款换货库存净变化更新为 0 |
| 后端全量 | `uv run pytest -q`：**179 passed**；`ruff check app tests` 通过 |
| 前端检查 | `npm run typecheck`、Docker `npm run build` 通过 |
| 前端 E2E | `npm run test:e2e`：**22 passed**；退货收货默认勾选回补，提示含「回补库存 1 件」 |
| 任务评测 | `uv run python -m app.scripts.task_eval`：19/19 通过 |
| 实机审计 | PostgreSQL 最新 `return_received`：`restock=true`，`restocked_items=[{"sku":"SKU-NCM-001","warehouse_id":"华东","qty":1}]` |
| 审计链 | `uv run python -m app.scripts.verify_audit`：merchant=5 OK |

## 边界

- 只有“回补/不回补”二选一，没有残次品库、质检状态、分级处置（翻新/报废/退回供应商）。
- 退货是按订单整单退回，回补整单商品数量；不支持按件部分退货或部分回补。
- 回补仓优先取原预留仓；旧订单无预留时回退到任一库存行，可能与实际发货仓不一致。
- 不回补时库存永久减少，系统不会记录不可售数量或损耗账。
- 回补不会触发补货、低库存恢复通知或财务成本核算。
- 并发下对库存行加锁再自增；未做多仓库调拨或库存流水表，仅保留当前可售数量与审计事件。
