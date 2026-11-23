# 库存调拨单与在途库存

- 验证时间：2026-11-09
- 范围：跨仓调拨从即时移库升级为可追踪调拨单：创建后在途、确认收货入库、取消回补调出仓
- 迁移：`f9c1e3a5b7d2`（`stock_transfers`）

## 行为

1. 新增 `stock_transfers`：调拨单号、SKU/标题快照、调出/调入仓、数量、状态（`in_transit/received/cancelled`）、备注、创建/收货/取消人与时间、取消原因。
2. 创建调拨单时锁定商品与调出仓库存：调出仓可售数量立即扣减并写 `transfer_out` 流水，备注包含调拨单号；数量不足 409，同仓调拨 422。
3. 在途期间不进入任何仓库可售库存；后台调拨单面板按状态展示在途数量、路径与时间。
4. 确认收货时锁定在途单与调入仓库存，调入仓自动建行并写 `transfer_in` 流水，进入 `received`；重复收货 409。
5. 取消在途单必须填写原因，按调出仓回补并写 `transfer_cancel_in` 流水，进入 `cancelled`；已收货/已取消不能再取消。
6. 创建、收货、取消分别写 `stock_transfer_created` / `stock_transfer_received` / `stock_transfer_cancelled` 审计；目标仓调出时沿用低库存告警。
7. 后台商品页新增「调拨单（在途库存）」面板：创建、按状态筛选、确认收货、填写原因取消；原有即时调拨按钮保留用于同仓/现场快速移库。
8. 库存导出增加「调拨取消回补」原因标签；库存流水仍可按原因查询。

## 验证结果

| 项 | 结果 |
|---|---|
| 后端测试 | `tests/test_stock_transfers.py`：创建后调出仓 10→7、在途列表；收货后调入仓 5→8、重复收货 409；取消回补调出仓并写 `transfer_cancel_in`；同仓 422、库存不足 409、未知 SKU/仓 404、客服 403 |
| 后端全量 | `.venv/Scripts/python.exe -m pytest -q`：**299 passed**；`ruff check app tests` 通过 |
| 前端检查 | `npm run typecheck`、Docker `npm run build` 通过 |
| 前端 E2E | `npx playwright test`：**39 passed**；新增「创建调拨单 → 在途 → 确认收货 → 调出仓 3、调入仓 2」 |
| 迁移 | 真实 PostgreSQL `upgrade head` → `downgrade -1` → `upgrade head` 通过 |
| 任务评测 | `python -m app.scripts.task_eval`：19/19 通过 |
| 审计链 | `python -m app.scripts.verify_audit`：merchant=5 OK |
| 容器 | api/web/worker 已重建；10 个服务 Up，api/postgres/mailpit 为 healthy |

## 边界

- 一张调拨单只包含一个 SKU；不支持多商品合并调拨、部分收货或拆单。
- 在途数量通过调拨单查询，不单独维护 `inventory.qty_in_transit` 列；盘点/对账只覆盖仓库可售库存。
- 没有调拨审批流、调拨价/成本结转、批次/序列号与运输承运商信息。
- 不支持自动收货、跨商家调拨、跨仓预留迁移或调拨超时提醒。
- 原有即时调拨仍保留，两个入口并存；新流程没有强制替代旧接口。
