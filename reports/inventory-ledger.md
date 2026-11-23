# 库存流水

- 验证时间：2026-11-04
- 范围：所有可售库存变动的流水、前后数量、原因与关联单据
- 迁移：`7036da747308`（`inventory_movements`，现有库存回填期初余额）

## 行为

1. 新增 `inventory_movements`，每次可售库存变化记录：商品、仓库、变动量、变动后数量、原因、关联订单/退货、操作人、备注。
2. 已接入的变动路径：
   - `product_created`：创建商品初始库存；
   - `admin_adjustment`：后台手工调整；
   - `checkout_reserved`：结算预留扣减；
   - `order_cancelled` / `order_expired`：取消/超时释放；
   - `return_restocked`：退货收货回补；
   - `exchange_allocated`：换货替换订单占用；
   - `opening_balance`：迁移时为现有库存回填。
3. 库存行加锁后由统一函数更新数量并写流水，数量不能为负；零变动不写流水。
4. 后台「商品库存」页新增库存流水查询：可按 SKU 查询，留空显示最近 50 条；展示时间、商品、仓库、变动、结存、原因、关联与操作人。
5. 接口 `GET /api/admin/inventory-movements?sku=&warehouse_id=&limit=`，店主/客服均可只读。

## 验证结果

| 项 | 结果 |
|---|---|
| 后端测试 | `tests/test_inventory_movements.py`：结算预留、取消释放、超时释放、手工调整、商品创建、退货回补、换货占用；SKU 过滤与客服只读 |
| 后端全量 | `uv run pytest -q`：**198 passed**；`ruff check app tests` 通过 |
| 前端检查 | `npm run typecheck`、Docker `npm run build` 通过 |
| 前端 E2E | `npm run test:e2e`：**24 passed**；新建商品并改库存后查询流水，看到「商品创建 +2」「手工调整 +5」 |
| 任务评测 | `uv run python -m app.scripts.task_eval`：19/19 通过 |
| 实机数据 | PostgreSQL 流水按原因统计：期初 61 条、结算 29 条、取消 2 条、退货回补 4 条、换货占用 2 条、商品创建 2 条、手工调整 2 条 |
| 审计链 | `uv run python -m app.scripts.verify_audit`：merchant=5 OK |

## 边界

- 流水从本迁移开始；此前库存只有回填的期初余额，不是完整历史。
- 只记录 `qty_available`；`qty_reserved` 的变化仍单独维护，不在库存流水里体现。
- 没有库存盘点、调拨、批次/序列号、成本或有效期；不生成补货建议。
- 原因枚举固定，后台备注是自由文本；没有按原因筛选；CSV 导出见 [inventory-export.md](inventory-export.md)，对账报表见 [inventory-reconciliation.md](inventory-reconciliation.md)。
- 流水不校验历史累计是否等于当前库存（期初余额 + 流水），可增加一致性检查。
- 订单/退货删除会将流水上的关联置空，但流水本身保留；当前产品没有删除订单/退货入口。
- 单商家多仓库，但没有仓库间调拨流水。
