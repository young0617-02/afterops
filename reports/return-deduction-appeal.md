# 退货质检扣减申诉

- 验证时间：2026-11-09
- 范围：顾客对已收货退货的质检扣减可在收货后 7 天内提交一次申诉（原因 + 最多 3 张凭证），待处理前可撤回；店主可支持（按核准金额生成追加退款提案）或驳回；顾客售后页、后台退货卡与 AI 退货进度均展示申诉状态
- 迁移：`c2e4f6a8b0d3`（`return_deduction_appeals`）

## 行为

1. 新表记录退货单、顾客、申诉原因、凭证、申请金额（默认等于该退货质检扣减合计）、状态（`pending`/`accepted`/`rejected`/`withdrawn`）、核准金额、处理说明、处理人与时间；同一退货单只能有一条申诉。
2. 顾客仅可对「已收货且质检扣减 > 0」的退货申诉，且须在收货后 7 天内；超过期限、无扣减、状态不符或重复提交分别返回 409。
3. `POST /api/store/returns/{id}/appeal` 提交申诉并通知员工；`POST /api/store/returns/{id}/appeal/withdraw` 撤回待处理申诉并通知员工；凭证复用既有顾客附件能力。
4. 店主 `POST /api/admin/returns/{id}/appeal/accept` 需填写 1 到扣减合计之间的核准金额，生成一条 `created_by=staff` 的追加退款提案（仍为待审批），申诉状态改为 `accepted`；`.../reject` 必须填写说明，状态改为 `rejected`，不生成退款提案。
5. 申诉处理写审计 `return_appeal_created` / `return_appeal_withdrawn` / `return_appeal_accepted` / `return_appeal_rejected`；支持与驳回均通知顾客，支持时带退款提案号与核准金额。
6. 退货视图新增 `appeal`、`appeal_deadline`、`can_appeal`；后台 `/api/admin/returns?status=appeal` 仅返回待处理申诉，计数新增 `appeals`。
7. 顾客售后卡展示申诉表单、截止时间、凭证、处理结果与撤回按钮；后台退货卡展示申诉原因/凭证，店主可填写核准金额与说明并支持或驳回。
8. AI `get_returns` 返回申诉状态与金额，退货进度回复展示「扣减申诉待处理（申请 X 元）」或「扣减申诉已支持（核准 X 元）」，引用仍为 `return:`。

## 验证结果

| 项 | 结果 |
|---|---|
| 后端测试 | `tests/test_return_appeals.py`：支持申诉生成 300 元追加退款提案、重复/越权/超期/无扣减/超额校验、撤回与驳回通知、后台筛选与计数 |
| 后端测试 | `tests/test_chat_after_sales_facts.py`：聊天退货进度显示申诉待处理与已支持核准金额 |
| 后端全量 | `.venv/Scripts/python.exe -m pytest -q`：**313 passed**；`.venv/Scripts/ruff check app tests` 通过 |
| 前端检查 | `npm run typecheck` 通过；Docker `npm run build` 随 web 重建通过 |
| 前端 E2E | 新增「顾客申诉质检扣减 → 店主核准 6 元 → 追加退款提案」用例；全量 **40 passed** |
| 迁移 | 真实 PostgreSQL `upgrade head` → `downgrade -1` → `upgrade head` 通过 |
| 任务评测 | `python -m app.scripts.task_eval`：19/19 通过 |
| 审计链 | `python -m app.scripts.verify_audit`：merchant=5 OK |
| 容器 | api/web/worker 已重建；10 个服务 Up，api/postgres/mailpit 为 healthy |

## 边界

- 每个退货单只能申诉一次；撤回或驳回后不能再次申诉。
- 申诉窗口固定为收货后 7 天，未做成商家可配置政策。
- 只覆盖质检扣减争议；不支持对整单驳回、维修结果、物流责任或非扣减金额发起申诉。
- 支持申诉生成的是新的待审批退款提案，不修改原扣减记录、不自动作废已执行退款；资金仍由店主审批后经 mock PSP 执行。
- 申诉处理不会自动重新质检，也不要求店主补传质检照片；顾客凭证不按退货明细逐件绑定。
- 待处理申诉没有 SLA、超时升级或自动分派。
