# 维修寄回与顾客确认

- 验证时间：2026-11-09
- 范围：店主完成维修时必须登记寄回运单，顾客确认收到后维修单才最终完成
- 迁移：`d6f8b0c2e4a7`（`repair_requests.return_carrier/return_tracking_no/repaired_at`、`return_shipped` 状态）

## 行为

1. 维修状态机扩展为 `requested → approved → in_transit → received → return_shipped → completed`；`return_shipped` 表示维修完成并已寄回，等待顾客签收。
2. `POST /api/admin/repairs/{id}/complete` 新增必填 `return_carrier` 与 `return_tracking_no`；缺少字段返回 422，缺少处理结果或状态不为 `received` 仍拒绝。
3. 完成后写入 `repaired_at`、寄回承运商/运单与处理结果，状态进入 `return_shipped`，不再直接进入 `completed`；写 `repair_completed` 审计并通知顾客「维修完成，已寄回」。
4. 新增 `POST /api/store/repairs/{id}/confirm-receipt`：仅维修所属顾客、仅 `return_shipped` 状态可确认；重复确认 409，未寄回就确认 409。
5. 顾客确认后状态进入 `completed` 并写 `completed_at`，审计 `repair_customer_received`，通知员工「顾客已确认收到维修件」。
6. 后台维修队列的「待处理」包含待顾客签收，新增筛选「待顾客签收」；后台卡片展示维修寄回承运商/运单与处理结果。
7. 顾客售后进度展示维修寄回运单与处理结果，并提供「确认收到维修件」按钮；状态标签区分「回寄中」「维修中」「维修寄回中」「已完成」。
8. 维修不产生退款；寄回物流仍为人工登记，不接承运商 API 或轨迹节点。

## 验证结果

| 项 | 结果 |
|---|---|
| 后端测试 | `tests/test_repairs_api.py`：完成缺寄回运单 422、未寄回确认 409、完成进入 `return_shipped` 并记录寄回字段、顾客确认进入 `completed`、重复确认 409、审计包含 `repair_completed` 与 `repair_customer_received` |
| 后端全量 | `.venv/Scripts/python.exe -m pytest -q`：**289 passed**；`ruff check app tests` 通过 |
| 前端检查 | `npm run typecheck`、Docker `npm run build` 通过 |
| 前端 E2E | `npx playwright test`：**38 passed**；维修用例增加「店主填写寄回运单 → 顾客看到维修寄回中 → 确认收到 → 已完成」 |
| 迁移 | 真实 PostgreSQL `upgrade head` → `downgrade -1` → `upgrade head` 通过 |
| 任务评测 | `python -m app.scripts.task_eval`：19/19 通过 |
| 审计链 | `python -m app.scripts.verify_audit`：merchant=5 OK |
| 容器 | api/web/worker 已重建；10 个服务 Up，api/postgres/mailpit 为 healthy |

## 边界

- 寄回物流只有承运商与运单号，没有承运商 API、轨迹节点、预计送达或电子面单。
- 顾客只能确认收到，不能拒收、发起争议或要求二次维修；异常需人工开新维修或走工单。
- 维修不记录备件成本、工时、报价与付费；仍不产生退款。
- 没有自动确认超时、签收凭证上传、序列号校验与维修报告导出。
- 维修完成后寄回记录不可修改；如需改单号只能人工处理数据。
