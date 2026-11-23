# 退款失败自动重试

- 验证时间：2026-11-12
- 范围：渠道失败的退款超过阈值后由 worker 自动重试，最多 3 次；成功重试后进入处理中并由自动结算收口
- 迁移：`e5a7c9d1b3f6_refund_settlement_retries.py`，`refunds` 新增 `settlement_failed_at`，并回填历史失败记录

## 行为

1. 财务手动标记渠道失败时写入 `settlement_failed_at`，保留失败原因与已尝试次数（批准执行时计 1 次）。
2. 迁移为存量 `failed` 退款回填失败时间（优先执行时间，其次创建时间），使旧失败记录也能进入自动重试。
3. worker 每轮先自动结算处理中退款，再扫描失败超过 `REFUND_AUTO_RETRY_MINUTES`（默认 15 分钟）且尝试次数小于 `REFUND_AUTO_RETRY_MAX_ATTEMPTS`（默认 3）的退款。
4. 自动重试逐条加锁校验：置回 `processing`、尝试次数 +1、清空失败原因/渠道引用/失败时间；写 `refund_settlement_auto_retried` 审计（含尝试次数、最大次数、阈值、操作人为空）。
5. 通知顾客「退款已重新提交渠道」，正文标注系统自动重试；后续由自动结算在下一轮标记到账并再通知顾客。
6. 达到最大尝试次数后保持 `failed`，不再自动重试，需财务人工处理；手动重试仍可用，手动重试会清空失败时间。
7. `REFUND_AUTO_RETRY_MINUTES=0` 或 `REFUND_AUTO_RETRY_MAX_ATTEMPTS=0` 关闭自动重试；财务页展示“失败超过 X 分钟自动重试，最多 Y 次”。
8. 自动重试只改变渠道结算状态，不重新执行退款账本、不改变订单退款状态、金额与发票。

## 验证结果

| 项 | 结果 |
|---|---|
| 后端测试 | `tests/test_refund_auto_settlement.py` 新增 2 例：失败 16 分钟后自动重试（processing、尝试 2 次、失败字段清空、审计 auto_retried、顾客通知，随后自动结算成功）；阈值 0、达到上限、二次失败后不再重试，手动失败原因保留 |
| 后端全量 | `.venv/Scripts/python.exe -m pytest -q`：**344 passed**；`.venv/Scripts/ruff check app tests` 通过 |
| 前端检查 | `npm run typecheck` 通过；web 容器重建构建通过 |
| 前端 E2E | 财务结算用例新增「自动重试」提示断言；全量 **44 passed** |
| 迁移 | dev Postgres `upgrade head → downgrade -1 → upgrade head` 成功，head = `e5a7c9d1b3f6`；存量失败记录回填逻辑已包含 |
| worker | 新镜像启动日志 `refund_auto_settle=5m retry=15m/3x`；dev 库当前无失败退款（1194 笔 succeeded），无实机重试积压 |
| 任务评测 | `NO_PROXY= python -m app.scripts.task_eval`：19/19 通过 |
| 审计链 | `python -m app.scripts.verify_audit`：merchant=5 OK |
| 容器 | 10 个服务 Up，api/postgres/mailpit healthy；`/health` 200、web 200 |

## 边界

- mock 渠道失败只由财务手动注入，自动重试在 mock 下必然成功；无真实拒付、错误码或退票。
- 固定 15 分钟间隔、最多 3 次，无指数退避、按错误码分类或按金额分层；尝试次数沿用现有执行次数口径。
- 达到上限后无告警升级或工单，仅保持 failed 等人工处理；财务仍可手动重试超过自动上限。
- 自动重试与自动结算分属两轮 worker，成功后一般需数分钟才变为已到账；不是实时 PSP 回调。
- 回填的存量失败时间不是真实失败时刻，可能立即触发一次自动重试；这是有意让旧失败进入恢复流程。
- 重试不会改变退款账本、补偿标记、发票或售后收款台账。
