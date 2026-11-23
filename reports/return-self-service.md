# 退货自助：撤回与补充凭证

- 验证时间：2026-11-04
- 范围：顾客撤回待审核退货、追加图片凭证、后台状态同步
- 迁移：`ef83416f6925`（`return_requests` 增加 `withdrawn` 状态）

## 接口

- `POST /api/store/returns/{id}/withdraw`：仅 `requested` 状态可撤回；写入 `return_withdrawn` 审计并通知客服。
- `POST /api/store/returns/{id}/attachments`：仅 `requested` 状态可追加凭证；合并去重后最多 3 张，逐张校验属于本人；写 `return_evidence_added` 审计并通知客服。
- `GET /api/admin/returns?status=withdrawn`：后台可按已撤回筛选；待处理队列自动排除已撤回。

## 规则

- 归属校验：他人退货返回 404。
- 撤回后同一订单可重新提交退货（`withdrawn` 不计入进行中状态）。
- 已批准/已寄回/已收货/已驳回不能撤回或补凭证；拒绝返回 409。
- 补凭证不改变退货状态，也不触发退款；退款/换货仍走店主审批。
- 顾客售后页对 `requested` 显示「补充凭证」和「撤回申请」操作；后台状态映射新增「已撤回」。

## 验证结果

| 项 | 结果 |
|---|---|
| 迁移 | `upgrade → downgrade -1 → upgrade` 通过，当前 head `ef83416f6925` |
| 后端测试 | `tests/test_returns_api.py`：撤回、重复撤回 409、撤回后审批 409、后台队列排除、同订单重新提交、补凭证到 3 张、第 4 张 422、批准后补凭证 409、审计事件 |
| 后端全量 | `uv run pytest -q`：**150 passed**；`ruff` 通过 |
| 前端 E2E | `npm run test:e2e`：**15 passed**；新增送达后退货 → 撤回 → 状态变「已撤回」 |
| 任务评测 | `uv run python -m app.scripts.task_eval`：16/16 通过 |
| 审计链 | `uv run python -m app.scripts.verify_audit`：OK |

## 边界

- 只能撤回待审核退货；已批准后需要联系店主/客服处理。
- 不能修改退货原因或处理方式（退款/换货），只能撤回后重新提交。
- 凭证最多 3 张，不支持删除、替换或视频；上传走现有附件存储。
- 撤回/补凭证不涉及资金；退款执行仍由店主审批提案。
