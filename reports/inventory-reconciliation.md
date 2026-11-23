# 库存对账

- 验证时间：2026-11-05
- 范围：按商品/仓库汇总库存流水，与当前可售库存比对，后台展示差异
- 依赖：库存流水 `inventory_movements`（迁移 `7036da747308`）

## 行为

1. 对每个库存行汇总全部 `qty_delta`，与当前 `qty_available` 比对。
2. 有流水记录的库存行计入「已跟踪」；流水累计等于当前库存计为「账实一致」，否则进入差异表。
3. 没有任何流水的库存行计入「未跟踪」，不参与差异判定（通常是迁移时 0 库存的旧行）。
4. 后台「商品库存 → 库存对账」展示已跟踪/账实一致/未跟踪/差异数量；差异表显示商品、仓库、当前库存、流水累计与差值。
5. 支持手动「重新对账」；店主与客服均可查看。
6. 接口 `GET /api/admin/inventory-reconciliation`。

## 验证结果

| 项 | 结果 |
|---|---|
| 后端测试 | `tests/test_inventory_reconciliation.py`：新商品创建+结算后账实一致；直接篡改库存后检出差异（当前 5 / 流水 3 / 差 2）；客服可读 |
| 后端全量 | `uv run pytest -q`：**207 passed**；`ruff check app tests` 通过 |
| 前端检查 | `npm run typecheck`、Docker `npm run build` 通过 |
| 前端 E2E | `npm run test:e2e`：**26 passed**；商品页对账面板显示账实一致、差异 0 |
| 任务评测 | `uv run python -m app.scripts.task_eval`：19/19 通过 |
| 实机数据 | PostgreSQL：88 个库存行，68 个已跟踪且全部匹配，20 个未跟踪（无流水），差异 0 |
| 审计链 | `uv run python -m app.scripts.verify_audit`：merchant=5 OK |

## 边界

- 只校验可售库存 `qty_available`，不校验 `qty_reserved`、预留单或订单占用。
- 未跟踪库存行只计数，不参与差异判断；迁移前历史没有流水。
- 对账是只读检查，不会自动修正差异，也不生成调整流水。
- 没有定时任务/告警；需要人工打开页面或调用接口。
- 差异只按商品+仓库汇总，不定位到具体订单或操作。
- 期初余额是迁移快照；跨库/外部系统直接改库存会产生差异，这是预期行为。
