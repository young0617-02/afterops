# 库存成本与报损金额

- 验证时间：2026-11-05
- 范围：商品成本价维护、库存成本估值、退货报损件数与成本损失
- 迁移：`b3d5f7a9c1e4`（`products.cost_cents`）

## 行为

1. 商品新增成本价 `cost_cents`，默认 0；创建、编辑与 CSV 导入（可选 `cost` 列，单位元）均可填写，负数被拒绝。
2. 成本价仅店主可见可改：客服商品列表与详情的 `cost_cents/cost_display` 返回 `null`，修改接口仍为店主权限。
3. `GET /api/admin/inventory-valuation` 仅店主可读；按商品成本价 ×（可售 + 预留）计算逐仓估值与总值，零在库行不返回，默认最多 200 行。
4. 结算预留期间在库数量为可售+预留，估值不因下单减少；支付提交预留后商品从在库中移除，估值随之下降。
5. 售后分析新增 `restocked_units`、`damaged_units`、`damaged_cost_cents`；报损成本按报损件数 × 商品**当前**成本价计算，回补件不计损失。
6. 后台商品页展示成本价、编辑入口与库存估值表（在库/成本价/估值），客服不可见；售后分析展示回补件数、报损件数与报损成本。
7. 商品成本变更写 `product_updated` 审计并带当前成本；CSV 导入审计包含成本字段。

## 验证结果

| 项 | 结果 |
|---|---|
| 后端测试 | `tests/test_inventory_valuation.py`：成本创建/编辑/负数 422、客服读取为 null 且修改 403；估值含预留、支付后下降、逐仓金额与总值、客服 403。`tests/test_partial_returns.py`：报损 1 件成本 100.00 元、回补 1 件、分析字段正确。`tests/test_product_import.py`：CSV 可选成本列落库 |
| 后端全量 | `uv run pytest -q`：**262 passed**；`ruff check app tests` 通过 |
| 前端检查 | `npm run typecheck`、Docker `npm run build` 通过 |
| 前端 E2E | `npm run test:e2e`：**33 passed**；商品用例覆盖新建成本、成本显示、编辑成本、库存估值按仓刷新（170.00/85.00 → 200.00） |
| 迁移 | 真实 PostgreSQL `upgrade head` → `downgrade -1` → `upgrade head` 通过 |
| 任务评测 | `uv run python -m app.scripts.task_eval`：19/19 通过 |
| 审计链 | `uv run python -m app.scripts.verify_audit`：merchant=5 OK |
| 容器 | api/web/worker 已重建；10 个服务 Up，api/postgres/mailpit 为 healthy |

## 边界

- 没有批次、FIFO 或移动加权；库存估值与报损成本使用商品当前成本价，改价会回溯影响历史估值/报损金额。
- 没有采购单、入库成本、COGS、毛利/利润表与税费口径；成本仅用于内部估值。
- 估值按可售+预留计算，不含在途调拨或残次品仓；零库存行不显示。
- 客服完全看不到成本，也没有按仓库/类目筛选估值或导出。
- CSV 成本列可选，缺省为 0；不校验成本是否高于售价。
- 前端估值表最多展示 20 行（接口最多 200 行），没有分页。
