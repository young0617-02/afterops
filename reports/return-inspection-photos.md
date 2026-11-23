# 退货质检照片

- 验证时间：2026-11-09
- 范围：店主确认收货前后上传退货质检照片，顾客与后台可查看
- 迁移：`f2b4d6e8a0c1`（`return_requests.inspection_attachment_ids`）

## 行为

1. `POST /api/admin/returns/{id}/inspection-attachments` 仅店主可用，multipart 上传 PNG/JPEG/WebP，沿用 5MB 限制；退货状态需为 `approved/in_transit/received`。
2. 每张退货最多 3 张质检照片；第 4 张返回 422，空/非图片沿用附件校验 422；工单不存在 404。
3. 员工附件记录上传人 `uploaded_by_user_id`，`customer_id` 为退货顾客；上传写 `return_inspection_attachment_added` 审计（退货、订单、附件 ID、数量、操作人）。
4. 退货视图新增 `inspection_attachment_ids`；顾客售后进度通过 `/api/store/attachments/{id}` 查看，后台退货卡通过 `/api/admin/attachments/{id}` 查看。
5. 后台退货卡提供「质检照片」选择入口与缩略图；顾客退货卡展示质检照片缩略图，点击可看原图。
6. 已收货后仍可由店主补充质检照片，便于留档；客服账号只读。
7. 照片与顾客上传的退货凭证分开存储，质检照片独立计数与展示。

## 验证结果

| 项 | 结果 |
|---|---|
| 后端测试 | `tests/test_return_inspection_photos.py`：3 张上传成功、第 4 张 422、非图片 422、顾客/后台读取原图、顾客视图字段、审计计数；未批准退货 409、缺失退货 404、客服 403 |
| 后端全量 | `uv run pytest -q`：**278 passed**；`ruff check app tests` 通过 |
| 前端检查 | `npm run typecheck`、Docker `npm run build` 通过 |
| 前端 E2E | `npm run test:e2e`：**36 passed**；多商品退货用例增加「店主上传质检照片 → 后台缩略图 → 顾客售后进度查看」 |
| 迁移 | 真实 PostgreSQL `upgrade head` → `downgrade -1` → `upgrade head` 通过 |
| 任务评测 | `uv run python -m app.scripts.task_eval`：19/19 通过 |
| 审计链 | `uv run python -m app.scripts.verify_audit`：merchant=5 OK |
| 容器 | api/web/worker 已重建；Docker 重启后 10 个服务 Up，api/postgres/mailpit 为 healthy |

## 边界

- 质检照片按整张退货单，不区分具体退货明细；没有逐件质检照片。
- 不支持删除/替换照片、照片标注、压缩、服务端缩略图或 EXIF 清洗；仅按文件头校验。
- 上传后不通知顾客，也没有「质检完成」状态；顾客只能被动查看。
- 没有质检报告导出、照片审核或对外分享链接。
- 照片链接依赖登录 Cookie（顾客本人/商家员工），不是签名 URL。
- 上传发生在收货前/后均可，没有强制在确认收货时完成质检。
