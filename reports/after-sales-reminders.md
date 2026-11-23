# 售后超时提醒

- 验证时间：2026-11-10
- 范围：worker 周期扫描四类卡滞售后节点，向对应角色各提醒一次；覆盖上门取件超预约日、维修报价待确认、退款渠道处理超时、扣减申诉待处理
- 迁移：`f0a2c4e6b8d1_after_sales_reminders.py`，为四张表新增提醒时间字段（`return_pickups.reminded_at`、`repair_requests.quote_reminded_at`、`refunds.settlement_reminded_at`、`return_deduction_appeals.reminded_at`）

## 行为

1. 上门取件：`status=scheduled` 且 `scheduled_date` 早于当天（UTC）且未提醒的预约，通知全体员工「上门取件已超预约日期」，正文含订单号、预约日期与时段、取件单号。
2. 维修报价：`quote_status=pending` 且报价发出超过 `AFTER_SALES_QUOTE_REMINDER_HOURS`（默认 24）且未提醒，通知顾客「维修报价待确认」，正文含订单号、维修费与报价说明，跳转售后页。
3. 退款结算：`settlement_status=processing` 且执行时间超过 `AFTER_SALES_SETTLEMENT_REMINDER_HOURS`（默认 24）且未提醒，通知全体员工「退款渠道处理超时」，正文含订单号、退款金额与渠道流水号。
4. 扣减申诉：`status=pending` 且创建超过 `AFTER_SALES_APPEAL_REMINDER_HOURS`（默认 24）且未提醒，通知全体员工「扣减申诉待处理超时」，正文含订单号与申请金额。
5. 每类每轮最多处理 50 条并使用行锁；四类处理完统一提交，返回 `pickup_overdue / quote_pending / refund_processing / appeal_pending` 计数。
6. 提醒后写入对应的 `reminded_at`，后续扫描不会重复提醒；状态被推进（取件完成/取消、报价响应、结算成功/失败、申诉处理）后自然退出扫描。
7. worker 每隔 `AFTER_SALES_REMINDER_INTERVAL_SECONDS`（默认 300 秒）调用一次，间隔设为 0 可关闭；有提醒时日志输出 `after-sales reminders {...}`。
8. 站内通知复用现有角色通道：员工提醒写入所有启用账号并走既有客服邮件通道；顾客提醒按顾客邮件偏好写入 outbox。

## 验证结果

| 项 | 结果 |
|---|---|
| 后端测试 | `tests/test_after_sales_reminders.py`：四类场景各造一条逾期数据，首次扫描统计 1/1/1/1、四个 `reminded_at` 均写入、通知类型与收件人正确；二次扫描全部为 0 且通知总数不变 |
| 后端全量 | `.venv/Scripts/python.exe -m pytest -q`：**330 passed**；`.venv/Scripts/ruff check app tests` 通过 |
| 前端 E2E | 全量 **42 passed**（api/web/worker 重建后运行） |
| 迁移 | 容器内 `alembic upgrade head` 应用；down 后提醒字段为 0，up 后为 4；`alembic current` = `f0a2c4e6b8d1 (head)` |
| 实机扫描 | 在新镜像的 api 容器内对生产 Postgres 直接调用服务，返回 `{pickup_overdue:0, quote_pending:0, refund_processing:0, appeal_pending:0}`（当前无逾期数据），四表查询与行锁均正常 |
| 任务评测 | `NO_PROXY= python -m app.scripts.task_eval`：19/19 通过 |
| 审计链 | `python -m app.scripts.verify_audit`：merchant=5 OK |
| 容器 | api/web/worker 已重建；10 个服务 Up，api/postgres/mailpit healthy；`/health` 200、web 200 |

## 边界

- 每个节点只提醒一次，不做升级、二次催办、静默时段或提醒确认；处理人需自行推进状态才会停止后续轮次。
- 阈值为部署级环境变量，不按商家/队列配置；上门取件按 UTC 日期判定超期，未做门店时区换算。
- 员工提醒按启用账号逐个生成，账号越多通知行越多；无值班表、轮班或只发给负责人的逻辑。
- 仅提醒，不自动取消取件、关闭报价、重试退款或裁决申诉；退款失败仍由财务手动重试。
- 每类每轮上限 50 条，积压超过时靠后续轮次逐步清空；无提醒历史页、统计面板或 Prometheus 指标。
- 依赖 worker 单实例运行；行锁可避免多实例重复提醒，但未做跨实例调度与补偿。
