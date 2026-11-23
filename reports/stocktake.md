# 库存盘点

- 验证时间：2026-11-05
- 范围：按商品/仓库录入实盘数量，差异直接调整可售库存并留痕
- 依赖：库存流水 `inventory_movements`（迁移 `7036da747308`）

## 行为

1. 店主在「商品库存」对某个仓库行填写数量，点击「盘点」提交 `POST /api/admin/products/{sku}/inventory/{warehouse_id}/stocktake`。
2. 服务先锁商品行、再锁库存行，读取当前可售数量作为盘点前值。
3. 实盘数与当前值不同时，按差值调整 `qty_available`，写一条 `reason=stocktake` 的库存流水（记录变动量、结存、操作人、备注）；相同时不写流水。
4. 无论是否有差异都写 `inventory_stocktake` 审计事件，载荷含 SKU、仓库、前后数量、差值、操作人与备注。
5. 接口返回 `sku / warehouse_id / before_qty / after_qty / delta`，前端提示「盘点完成：前值 → 后值」并刷新商品卡、流水与对账面板。
6. 盘点只能由店主执行；客服账号返回 403。实盘数量必须 ≥ 0，否则 422；商品或库存行不存在返回 404。

## 验证结果

| 项 | 结果 |
|---|---|
| 后端测试 | `tests/test_stocktake.py`：10 → 盘点 7（响应 delta -3、流水 -3、审计备注）；同数量盘点 delta 0 且不新增流水；负数 422；客服 403 |
| 后端全量 | `uv run pytest -q`：**218 passed**；`ruff check app tests` 通过 |
| 前端检查 | `npm run typecheck`、Docker `npm run build` 通过 |
| 前端 E2E | `npm run test:e2e`：**28 passed**；商品用例覆盖 2 → 手工调整为 7 → 盘点为 6，流水出现「库存盘点 / -1」 |
| 任务评测 | `uv run python -m app.scripts.task_eval`：19/19 通过 |
| 实机数据 | 盘点后库存对账差异 0；流水原因计数包含 `stocktake` |
| 审计链 | `uv run python -m app.scripts.verify_audit`：OK |

## 边界

- 盘点直接生效，没有盘点单、冻结期、复核或审批流；不区分盘盈/盘亏原因。
- 只调整可售库存 `qty_available`，不处理 `qty_reserved`、在途或锁定库存。
- 不按批次、库位、成本计价，也不生成损耗/成本凭证。
- 零差异盘点只写审计、不写库存流水，因此流水表不能当作「每次盘点都发生过」的完整盘点记录。
- 没有定时盘点任务、差异阈值告警或导出；需人工在页面操作。
- 并发盘点依赖行锁串行化，后提交者基于前一次结果计算差值；没有版本号冲突提示。
