# 商品 CSV 批量导入

- 验证时间：2026-11-05
- 范围：店主上传 CSV 一次创建多个商品与默认仓初始库存
- 依赖：现有商品/库存/库存流水/审计；无新迁移

## 行为

1. 店主在后台「商品库存 → 批量导入 CSV」选择 UTF-8 CSV；接口 `POST /api/admin/products/import`，客服 403。
2. 必填列：`sku,title,price`；可选 `category,brand,initial_qty,warranty_months,status,support_7d_no_reason`。价格单位为元，最多两位小数；SKU 统一转大写。
3. 限制：文件 ≤1MB、最多 200 行、SKU 3–64 位字母/数字/短横线、标题 ≤255 字、类目/品牌 ≤80 字、库存 0–1,000,000、保修 0–120 个月、状态 `draft/active/archived`。
4. 整批先校验后写入：任意一行出错返回 422 及行号原因，**不会创建任何商品**；文件内 SKU 重复、数据库已存在 SKU 也整批拒绝（409）。
5. 校验通过后单事务创建全部商品：每个商品建默认仓库存行、写 `product_created` 库存流水和商品创建审计（`source=csv_import`），最后写一条 `products_imported` 汇总审计。
6. active 商品若初始库存为 0 或低于阈值，按既有低库存规则通知客服。
7. 成功后后台提示「已导入 N 个商品」并刷新列表；创建的商品与手工新建一致，可继续编辑、上下架、盘点、调拨。

## 验证结果

| 项 | 结果 |
|---|---|
| 后端测试 | `tests/test_product_import.py`：两行导入成功（价格/库存/保修/状态/店面可见、库存行与汇总审计）；坏价格整批 422 且不落库；文件内重复 422；已存在 SKU 409；缺列 422；客服 403 |
| 后端全量 | `uv run pytest -q`：**243 passed**；`ruff check app tests` 通过 |
| 前端检查 | `npm run typecheck`、Docker `npm run build` 通过 |
| 前端 E2E | `npm run test:e2e`：**32 passed**；后台上传 CSV 后提示导入 1 个商品，卡片显示标题、库存 4、保修 6 个月 |
| 任务评测 | `uv run python -m app.scripts.task_eval`：19/19 通过 |
| 审计链 | `uv run python -m app.scripts.verify_audit`：merchant=5 OK |
| 容器 | api/web 已重建；10 个服务 Up，api/postgres/mailpit 为 healthy |

## 边界

- 只创建新商品，不更新已有 SKU；需要改价/改名/补库存请用后台编辑或库存接口。
- 没有独立 dry-run 预览接口；上传即整批校验，失败不落库但也不返回可下载的错误文件。
- 不导入描述、规格、图片、详情图、条码、供应商或成本；价格只支持元两位小数。
- 不支持部分成功、逐行跳过、异步任务、进度或导入历史页面；汇总只在审计事件 `products_imported`。
- CSV 必须 UTF-8（可带 BOM），不支持 XLSX；文件超过 1MB 或 200 行直接拒绝。
- 批量 active 商品可能一次触发多条低库存通知/邮件。
