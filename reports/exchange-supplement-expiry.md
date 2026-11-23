# 补差超时提醒与自动取消换货

- 验证时间：2026-11-10
- 范围：换货补差价待支付时，收货 24 小时后提醒顾客一次；收货 72 小时后仍未支付则 worker 自动取消换货（释放库存、生成原订单退款提案）
- 迁移：`b2d4f6a8c0e1_exchange_supplement_expiry.py`，`return_requests` 新增 `exchange_supplement_reminded_at`

## 行为

1. 补差待支付时，退货视图新增 `exchange_payment_deadline` = 收货时间 + `AFTER_SALES_SUPPLEMENT_TIMEOUT_HOURS`（默认 72）；超时关闭时为空。顾客售后页与后台退货列表展示截止时间与“逾期自动取消”提示。
2. 售后提醒扫描新增第五类：补差待支付且收货超过 `AFTER_SALES_SUPPLEMENT_REMINDER_HOURS`（默认 24）且未提醒，通知顾客「换货补差价待支付」，正文含订单号、补差金额、支付截止时间与自动取消说明。
3. 提醒后写入 `exchange_supplement_reminded_at`，只提醒一次；已支付、已取消或状态已推进的换货不再命中。
4. worker 每 `AFTER_SALES_REMINDER_INTERVAL_SECONDS` 先执行超时取消、再执行提醒；扫描待支付、收货时间早于 `AFTER_SALES_SUPPLEMENT_TIMEOUT_HOURS`（默认 72）、补差大于 0 的换货，每轮最多 50 条。
5. 自动取消复用人工取消服务：替换订单未发货时释放 `committed` 库存预留、回补同仓并写 `exchange_cancelled_restock` 流水；替换订单置 cancelled/refunded；退货状态 `exchange_cancelled`、支付状态 `cancelled`、操作方 `system`。
6. 自动取消按退货规则生成原订单退款提案（仍需店主审批）；原因记录为「补差超时 N 小时未支付，系统自动取消换货」。
7. 自动取消通知全体员工（标题「补差超时换货已自动取消」）并通知顾客；写 `exchange_cancelled` 审计，载荷含 `cancelled_by=system` 与退款提案；售后时间线显示取消原因、操作方「系统自动」与退款提案。
8. 超时阈值设为 0 时 worker 不自动取消；提醒阈值与超时阈值独立配置。已支付补差、已发货替换订单或已取消换货不会被自动取消。
9. 并发保护：取消服务使用行锁并校验状态；顾客在 worker 执行前支付成功的，自动取消会因状态变化而跳过。

## 验证结果

| 项 | 结果 |
|---|---|
| 后端测试 | `tests/test_exchange_supplement_expiry.py`：24h 提醒一次且文案含截止/自动取消、72h 自动取消并释放库存、生成 89900 元提案、系统审计与双方通知、时间线「系统自动」、二次扫描 0；71h 与已支付不取消；阈值 0 不取消。`tests/test_after_sales_reminders.py` 更新五类统计 |
| 后端全量 | `.venv/Scripts/python.exe -m pytest -q`：**336 passed**；`.venv/Scripts/ruff check app tests` 通过 |
| 前端检查 | `npm run typecheck` 通过；web 容器重建构建通过 |
| 前端 E2E | 补差取消用例新增截止时间断言；全量 **43 passed** |
| 迁移 | dev Postgres `upgrade head → downgrade -1 → upgrade head` 成功，head = `b2d4f6a8c0e1` |
| worker | 新镜像启动日志含 `supplement_timeout=72h`；无取消任务时按周期静默扫描 |
| 实机扫描 | api 容器内对 dev Postgres 执行 `auto_cancel_overdue_exchange_supplements(hours=72)` 返回 0（无逾期数据），SQL 与字段路径正常 |
| 任务评测 | `NO_PROXY= python -m app.scripts.task_eval`：19/19 通过 |
| 审计链 | `python -m app.scripts.verify_audit`：merchant=5 OK |
| 容器 | 10 个服务 Up，api/postgres/mailpit healthy；`/health` 200、web 200 |

## 边界

- 阈值是部署级环境变量，不按商家/商品配置；时间按 UTC 计算，未做门店时区换算。
- 自动取消只生成退款提案，不自动退款；仍需店主审批，失败或驳回不恢复换货。
- 提醒只发一次，无二次催付、短信/IM、静默时段或提醒确认；顾客关闭售后邮件偏好时仅收站内通知。
- 每轮每类最多 50 条；worker 单实例，靠行锁与状态校验避免重复取消。
- 没有宽限期、人工延期、改价重报或部分明细取消；替换订单一旦发货即不再自动取消。
- 自动取消后替换订单为零元 cancelled/refunded 订单，不产生退款流水；原订单退款才进入退款账本与渠道结算。
- 关闭超时（设为 0）后仍会发送 24 小时提醒，文案不再包含自动取消日期。
