# 商品评价

- 验证时间：2026-11-05
- 范围：已签收订单逐件评价、图片凭证、商品页评分与评价列表、店主隐藏/恢复
- 迁移：`571815bce88b`（`product_reviews`）、`c1d3e5f7a9b2`（评价图片）、`d2f4a6c8e0b1`（商家回复）

## 行为

1. 顾客对已签收/已完成订单中的商品提交 1–5 星评分、可选文字（最多 2000 字）与最多 3 张 PNG/JPEG/WebP 图片；每个订单商品只能评价一次。
2. 评价必须来自该顾客自己的已签收订单；未签收、商品不属于订单、重复评价都会被拒绝。
3. 图片复用附件上传接口 `POST /api/store/attachments`，服务校验文件头、大小与附件归属；评价保存附件 ID。
4. 顾客在「我的订单 → 查看详情」中看到评价入口与图片选择；提交后显示「已评价 X 星」与缩略图。
5. 商品页展示可见评价的平均分、总数与最近 50 条评价；可见评价图片通过公开接口按「评价 ID + 附件 ID」读取，不暴露其他附件。
6. 后台「评价」页可筛选全部/展示中/已隐藏；店主可隐藏或恢复，客服只读；后台展示图片缩略图。
7. 隐藏后商品页与公开图片接口均不可见；后台仍可通过受权限保护的附件接口查看图片；评价记录与审计保留。
8. 店主可对任意评价（含已隐藏）回复一次并编辑，留空可清空；回复最多 1000 字，商品页与顾客订单页展示「商家回复」。
9. 每次创建评价写 `review_created`（含附件数量），隐藏/恢复写 `review_visibility_changed` 审计；回复/修改/清空写 `review_replied` 审计。

## API

| 方法 | 路径 | 权限 | 说明 |
|---|---|---|---|
| GET | `/api/store/products/{sku}/reviews` | 公开 | 可见评价、平均分、数量与图片 URL |
| GET | `/api/store/reviews/{id}/images/{attachment_id}` | 公开 | 仅可见评价内图片 |
| GET | `/api/store/reviews/mine` | 顾客 | 我的评价与图片 URL |
| POST | `/api/store/reviews` | 顾客 | `{order_no, sku, rating, content, attachment_ids}` |
| GET | `/api/admin/reviews?status=` | 店主/客服 | 全部/visible/hidden，含图片 URL |
| PATCH | `/api/admin/reviews/{id}` | 店主 | `{status: visible/hidden}` |
| PATCH | `/api/admin/reviews/{id}/reply` | 店主 | `{reply}`；空字符串清空 |

## 验证结果

| 项 | 结果 |
|---|---|
| 后端测试 | `tests/test_reviews_api.py`：创建与公开汇总、我的评价、重复 409、未签收 409、错误商品/非法评分 422、越权 404、隐藏后公开列表消失、恢复可见、客服 403、审计；上传图片后评价返回 URL、公开图片 200、隐藏后公开图片 404 但管理员仍可读；店主回复/修改/清空、公开可见、客服 403、审计 |
| 后端全量 | `uv run pytest -q`：**235 passed**；`ruff check app tests` 通过 |
| 前端检查 | `npm run typecheck`、Docker `npm run build` 通过 |
| 前端 E2E | `npm run test:e2e`：**31 passed**；确认收货后带 1 张图提交 5 星评价，后台回复后商品页显示商家回复，隐藏后评价消失 |
| 迁移 | 真实 PostgreSQL `upgrade head` → `downgrade -1` → `upgrade head` 通过 |
| 任务评测 | `uv run python -m app.scripts.task_eval`：19/19 通过 |
| 审计链 | `uv run python -m app.scripts.verify_audit`：merchant=5 OK |

## 边界

- 每个订单商品只能评价一次，不支持修改、删除、追评或更换图片。
- 最多 3 张图片，本地文件存储，无对象存储/CDN、压缩、缩略图或病毒/内容审核。
- 图片只随评价创建，创建后不能单独删除；隐藏评价会同时隐藏公开图片，但不删除文件。
- 公开列表只返回最近 50 条与全量平均分/总数，没有分页、排序、筛选或按图片筛选。
- 商品列表/搜索接口不带评分汇总，只有商品详情页展示。
- 商家只能回复一次并编辑/清空，不是对话线程；没有有用投票、举报、敏感词过滤或评价奖励。
- 顾客称呼来自账户名称；未设置时显示「匿名顾客」，不会展示手机号。
