# 库存与流水 CSV 导出

- 验证时间：2026-11-05
- 范围：当前库存快照与库存流水 CSV 下载，支持 SKU/仓库/日期过滤
- 依赖：现有 `products`、`inventory`、`inventory_movements`；无新迁移

## 行为

1. `GET /api/admin/inventory/export.csv` 导出当前库存快照：SKU、商品、类目、仓库编号/名称、可售、预留、更新时间。
2. `GET /api/admin/inventory-movements/export.csv` 导出库存流水，支持 `sku`、`warehouse_id`、`from`、`to` 查询参数；按时间倒序，单次最多 10000 行。
3. 流水字段：时间、SKU、商品、仓库编号/名称、变动、结存、原因（中文标签）、订单号、退货 ID、操作人邮箱、备注。
4. CSV 使用 UTF-8 BOM，Excel 直接打开中文不乱码；文件名分别为 `inventory.csv`、`inventory-movements.csv`。
5. 文本单元格以 `= + - @`、Tab 或回车开头时加前导单引号，避免 CSV 公式注入；数值字段（含负数变动）保持数值，不加引号。
6. 店主与客服均可导出；未登录返回 401。后台「商品库存」提供「导出库存」和「导出流水」按钮；流水导出沿用当前 SKU 查询条件。

## 验证结果

| 项 | 结果 |
|---|---|
| 后端测试 | `tests/test_inventory_export.py`：库存快照含 SKU/仓库/数量、UTF-8 BOM、`=SUM(1,1)` 标题被加单引号；流水按 SKU 过滤、包含中文原因、负数变动 `-2` 保持数值、未来日期返回仅表头、客服可导出、未登录 401 |
| 后端全量 | `uv run pytest -q`：**239 passed**；`ruff check app tests` 通过 |
| 前端检查 | `npm run typecheck`、Docker `npm run build` 通过 |
| 前端 E2E | `npm run test:e2e`：**31 passed**；商品库存用例触发两个下载，校验文件名与 CSV 内 SKU/「库存盘点」内容 |
| 任务评测 | `uv run python -m app.scripts.task_eval`：19/19 通过 |
| 审计链 | `uv run python -m app.scripts.verify_audit`：merchant=5 OK |
| 容器 | Docker Desktop 重启后整套 Compose 重建；api/web 已更新，10 个服务 Up |

## 边界

- 导出是同步内存生成，没有后台任务、分页下载或进度；流水最多 10000 行，超出会截断且不提示。
- 后台 UI 只提供库存全量导出与按当前 SKU 的流水导出；仓库与日期过滤仅 API 支持，没有前端控件。
- 库存快照包含草稿/下架商品与所有仓库行，不区分可售状态；没有成本、金额、批次或库龄。
- 流水包含操作人邮箱与备注，属于内部数据；没有脱敏选项或列选择。
- CSV 无 Excel 样式、合计行、多工作表；字段顺序固定。
- 不导出对账差异、盘点单或调拨单；这些仍只在页面查看。
- 公式注入防护只针对文本单元格；其他 CSV 消费端仍应按不可信输入处理。
