# 商品主图与详情图画廊

- 验证时间：2026-11-04
- 范围：店主上传/替换/移除商品主图；上传/删除最多 6 张详情图；店面展示
- 迁移：`746d6c845172`（主图）、`7bb24e5b40cf`（gallery_images）

## 接口

- `POST /api/admin/products/{sku}/image`：店主上传主图 PNG/JPEG/WebP，最大 5MB；写 `product_image_updated` 审计。
- `GET /api/admin/products/{sku}/image`：客服/店主读取主图（含草稿/下架）。
- `DELETE /api/admin/products/{sku}/image`：店主移除主图。
- `POST /api/admin/products/{sku}/images`：店主上传详情图，最多 6 张；写 `product_gallery_updated` 审计。
- `GET /api/admin/products/{sku}/images/{storage_name}`：后台读取详情图。
- `DELETE /api/admin/products/{sku}/images/{storage_name}`：店主删除详情图并清理文件。
- `GET /api/store/products/{sku}/images/{storage_name}`：公开读取在售商品详情图。
- 店面商品详情返回 `gallery_urls`；列表继续使用主图。

## 存储与安全

- 主图与详情图都存 `UPLOAD_DIR/products/`，UUID 文件名，路径穿越校验。
- 只接受 PNG/JPEG/WebP，按文件头判断；大小受 `UPLOAD_MAX_BYTES` 限制。
- 替换/删除在数据库提交后清理旧文件；数据库失败回滚并清理新文件。
- 店面只暴露在售商品图；草稿/下架图仅登录后台可读。
- 备份脚本归档整个 uploads 卷，主图与详情图一起备份。

## 验证结果

| 项 | 结果 |
|---|---|
| 后端测试 | `tests/test_product_images_api.py`：主图上传/替换/删除、详情图 6 张上限、第 7 张 409、公开读取、删除后公开 404、下架后公开 404、后台读取、agent 上传 403 |
| 迁移 | 两个迁移均 `upgrade → downgrade → upgrade` 通过，当前 head `7bb24e5b40cf` |
| 后端全量 | `uv run pytest -q`：**159 passed**；`ruff` 通过 |
| 前端 E2E | `npm run test:e2e`：**17 passed**；店主创建商品 → 上传主图与详情图 → 店面详情显示主图与 1 张详情图 |
| 任务评测 | `uv run python -m app.scripts.task_eval`：19/19 通过 |
| 审计链 | `uv run python -m app.scripts.verify_audit`：OK |

## 边界

- 每商品 1 张主图 + 最多 6 张详情图；无视频、无排序拖拽、无批量管理。
- 无对象存储/CDN，文件落本地卷；多实例部署需要共享存储。
- 无图片内容审核、病毒扫描、自动压缩或缩略图。
- 替换/移除只改当前商品图，不影响历史订单快照（快照不含图片）。
- 店面图片缓存 24 小时；旧版本 URL 仍可能被浏览器缓存。
