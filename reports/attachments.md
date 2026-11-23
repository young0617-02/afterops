# 售后图片凭证

- 验证时间：2026-11-03
- 范围：退货申请与待审退款提案的图片证据上传、绑定、按权限读取
- 存储：本地卷 `uploads_data` → `/data/uploads`；迁移 `01715d79f43c`

## 已落地

- `POST /api/store/attachments`：顾客上传，最大 5MB，仅 PNG/JPEG/WebP；按文件头识别类型，不信任客户端 MIME。
- 存储名使用 UUID，原始文件名只用于展示；SHA-256 落库。
- 退货申请可绑定最多 3 个附件；绑定校验附件属于当前顾客。
- 待审退款提案可由顾客补充最多 3 张凭证，写 `proposal_evidence_added` 审计并通知客服。
- `GET /api/store/attachments/{id}`：仅附件所属顾客可读。
- `GET /api/admin/attachments/{id}`：仅同商家客服/店主可读。
- 响应使用 `inline`、正确 content-type 与全局 `nosniff` 安全头；不存在/越权统一 404。
- 顾客售后页可选图上传并在退货卡展示缩略图；后台退货卡也可查看。

## 验证结果

| 项 | 结果 |
|---|---|
| 后端测试 | `tests/test_attachments_api.py` + `test_after_sales_api.py`：上传、SHA/大小、顾客读取、他人 404、admin/agent 读取、伪装文本 422、超 5MB 422；退货与提案绑定附件 |
| 后端全量 | `uv run pytest -q`：**127 passed**；`ruff` 通过 |
| 前端 E2E | `npm run test:e2e`：11 passed；退货流程与退款提案均可上传真实 1×1 PNG，后台缩略图实际加载 |
| 任务评测 | `uv run python -m app.scripts.task_eval`：14/14 通过 |

## 边界

- 只支持图片，不提供病毒扫描、内容审核、缩略图生成或对象存储/多副本。
- 本地卷单实例；备份演练已归档 uploads 并做 SHA-256 校验，但尚未自动恢复附件到线上卷。
- 没有附件删除/替换、总量配额或按用户全局限流。
- 仅退货与待审退款提案支持附件；聊天消息本身不支持附件。
- 文件大小上限 5MB、最多 3 张为服务端与接口校验，不是生产媒体处理能力。
