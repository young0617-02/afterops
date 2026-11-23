# 商品评价追评

- 验证时间：2026-11-05
- 范围：原评价后 30 天内追加一次文字+图片追评
- 迁移：`c9e1b3d5f7a0`（`product_reviews.follow_up_content / follow_up_attachment_ids / followed_up_at`）

## 行为

1. 顾客可对本人可见评价提交一次追评：内容必填、≤1000 字，最多 3 张图；原评价创建超过 30 天或已追评返回 409。
2. 追评不改变原评分与原评价内容；商家回复仍可独立保存，评价隐藏后不能追评，隐藏评价同时隐藏追评与两组图片。
3. 商品公开评价接口新增 `follow_up_content`、`follow_up_image_urls`、`followed_up_at`；追评图片复用评价图片路由，只对可见评价开放。
4. 顾客订单页在「已评价」卡片下提供追评输入、图片与提交按钮；提交成功后展示追评内容与图片。
5. 商品详情页与后台评价管理同步展示追评与图片；后台图片走 `/api/admin/attachments/{id}`。
6. 追评写 `review_followed_up` 审计（评价 ID、订单、SKU、图片数），不记录正文。
7. 原有评价、商家回复、隐藏/恢复与 CSAT 逻辑不变。

## 验证结果

| 项 | 结果 |
|---|---|
| 后端测试 | `tests/test_reviews_api.py`：追评落库/图片公开读取/后台展示/审计；重复追评 409、空内容 422、隐藏评价 409、超过 30 天 409、跨顾客 404 |
| 后端全量 | `uv run pytest -q`：**272 passed**；`ruff check app tests` 通过 |
| 前端检查 | `npm run typecheck`、Docker `npm run build` 通过 |
| 前端 E2E | `npm run test:e2e`：**35 passed**；评价用例扩展为「初评+图片 → 追评+图片 → 商品页/后台展示共 2 张图」 |
| 迁移 | 真实 PostgreSQL `upgrade head` → `downgrade -1` → `upgrade head` 通过 |
| 任务评测 | `uv run python -m app.scripts.task_eval`：19/19 通过 |
| 审计链 | `uv run python -m app.scripts.verify_audit`：merchant=5 OK |
| 容器 | api/web/worker 已重建；10 个服务 Up，api/postgres/mailpit 为 healthy |

## 边界

- 每条评价只能追评一次，不能编辑/删除追评，也没有二次追评。
- 追评没有独立评分，不重新计算商品平均分；评分只取初评。
- 超过 30 天不可追评；隐藏评价不可追评，管理员恢复可见后仍可在窗口内追评。
- 追评不触发顾客/商家通知，也不进入低分评价告警逻辑。
- 没有追评审核/敏感词过滤；图片沿用 5MB/3 张限制，不做压缩或 EXIF 清洗。
- 商家回复针对整条评价，不能单独回复追评。
