# 商品与库存管理

> 后续更新：商品已支持店主上传主图并在店面展示，见 [product-images.md](product-images.md)。本页验证数字与边界为当时快照。

- 验证时间：2026-11-03（补充低库存告警）
- 范围：店主新建/编辑商品、上下架、按仓库设置可售库存；客服只读
- 入口：`/admin` → 商品库存

## 接口

- `GET /api/admin/products?q=&status=&limit=`：客服/店主，含草稿与下架商品。
- `GET /api/admin/products/{sku}`：详情 + 各仓可售/预留。
- `POST /api/admin/products`：店主创建，SKU 大写规整，可带初始库存。
- `PATCH /api/admin/products/{sku}`：店主编辑标题/类目/品牌/描述/参数/价格/状态。
- `PUT /api/admin/products/{sku}/inventory/{warehouse_id}`：店主设置仓库可售库存，可创建新仓。
- 商品创建、商品更新、库存更新写哈希链审计。
- 可售库存从阈值上方跌破时通知客服并写 outbox 邮件；重复跌破不重复告警，补货到阈值上方后再次跌破才再次告警。

## 验证结果

| 项 | 结果 |
|---|---|
| 后端测试 | `tests/test_admin_products_api.py` + `test_low_stock_alerts.py`：创建/重复 SKU、编辑、上下架、店面可见性、库存多仓、负数拒绝、审计链、agent 只读、低库存告警与下单扣减触发 |
| 后端全量 | `uv run pytest -q`：**121 passed**；`ruff` 通过 |
| 前端 E2E | `npm run test:e2e`：**10 passed**；店主新建商品 → 调库存 → 店面详情可见价格与库存；客服商品页只读 |
| 任务评测 | `uv run python -m app.scripts.task_eval`：14/14 通过 |
| 浏览器实测 | msedge 1440×1000：商品列表、创建表单、编辑/库存行、状态与库存读数正常 |

## 边界

- 商品支持 CSV 批量导入（见 [product-import.md](product-import.md)），但只创建不更新、不导入描述/规格/图片；仍无视频、条码、供应商/采购单与安全库存预警。
- 可售库存为直接设置，不自动补货；预留库存单独展示且不能由此接口修改。
- SKU 创建后不可改；价格修改即时生效，没有审批流或生效时间。
- 下架仅影响店面可售，不删除历史订单/购物车数据；草稿商品不会出现在店面。
- 商品管理是店主操作，客服只读；未做操作人二次确认。
