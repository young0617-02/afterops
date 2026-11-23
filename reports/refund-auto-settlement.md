# 退款渠道自动结算（mock PSP）

- 验证时间：2026-11-12
- 范围：worker 将超过 `REFUND_AUTO_SETTLE_MINUTES`（默认 5 分钟）仍处于渠道处理中的退款自动标记到账，写入 mock 渠道引用、审计并通知顾客；财务手动标记与失败重试保留
- 迁移：无（复用现有退款结算字段）

## 行为

1. 退款账本执行后进入 `settlement_status=processing`；worker 每轮售后任务扫描执行时间超过 5 分钟的处理中退款，每轮最多 50 条。
2. 逐条加行锁并校验状态为 `processing`；自动标记 `succeeded`，生成 `PSP-AUTO-XXXXXXXXXXXX` 渠道引用，写入 `settled_at` 并清空失败原因。
3. 写 `refund_settlement_auto` 审计，载荷含退款、订单、金额、渠道引用、`auto_minutes=5` 与 `operator_user_id=null`，区分人工结算。
4. 通知顾客「退款已到账」，正文含订单号、金额与渠道引用；售后时间线展示退款到账事件。
5. 财务页在渠道结算区展示自动结算提示：处理中超过 X 分钟自动标记到账；仍可提前手动标记到账/失败，失败退款仍需人工重试。
6. `REFUND_AUTO_SETTLE_MINUTES=0` 关闭自动结算；阈值是部署级配置，不按商家区分。
7. 并发保护：自动与人工同时处理时，行锁 + 状态校验只允许一方成功；已自动结算的退款再手动结算返回 409。
8. 自动结算只改变渠道结算状态与通知，不改变退款账本、订单退款状态或金额。

## 验证结果

| 项 | 结果 |
|---|---|
| 后端测试 | `tests/test_refund_auto_settlement.py`：回拨执行时间 6 分钟后自动结算 1 笔（`PSP-AUTO-`、已到账时间、失败原因为空、尝试次数不变）、审计 `refund_settlement_auto` 与顾客通知、二次扫描为 0、再手动结算 409；未到期与阈值 0 不结算，手动结算仍成功 |
| 后端全量 | `.venv/Scripts/python.exe -m pytest -q`：**341 passed**；`.venv/Scripts/ruff check app tests` 通过 |
| 前端检查 | `npm run typecheck` 通过；web 容器重建构建通过 |
| 前端 E2E | 财务结算用例新增「自动标记渠道到账」提示断言；全量 **44 passed**（人工标记到账流程仍通过） |
| worker 实机 | 新镜像启动日志 `refund_auto_settle=5m`；以 10 秒间隔运行 worker 循环，`auto-settled 9 refunds`；dev 库退款结算状态最终全部为 succeeded（1164 笔 succeeded，5 笔为刚创建的短期处理中） |
| 任务评测 | `NO_PROXY= python -m app.scripts.task_eval`：19/19 通过 |
| 审计链 | `python -m app.scripts.verify_audit`：merchant=5 OK |
| 容器 | 10 个服务 Up，api/postgres/mailpit healthy；`/health` 200、web 200 |

## 边界

- mock PSP 自动成功，没有真实渠道回调、拒付、错误码、退票或对账文件；`PSP-AUTO-` 引用仅为本地模拟。
- 渠道失败不会自动重试，仍需财务手动重试；自动结算只处理 `processing`，不处理 `failed`。
- 自动结算阈值按部署配置，所有商家一致；worker 单实例，每轮最多 50 条，无按退款金额/账期分层。
- 自动结算与人工操作存在毫秒级竞态，系统用行锁和状态校验保证不重复；财务可能看到列表刷新后才变为已到账。
- 退款金额与订单状态在批准执行时已确定；自动结算不改变账本、发票或售后收款台账。
- 没有任何重试上限、退避或告警；如果 mock 渠道未来模拟失败，当前逻辑不会自动处理。
