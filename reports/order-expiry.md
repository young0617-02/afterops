# 未支付订单自动超时释放

- 验证时间：2026-11-03
- 范围：库存预留到期扫描、订单取消、库存回补、支付失败、审计与顾客通知
- 触发：worker 每 `ORDER_EXPIRE_INTERVAL_SECONDS`（默认 60 秒）执行

## 修复的缺口

此前 `expire_reservations` 只是手工脚本，worker 没有调度；未支付订单会一直占用 `qty_reserved`，库存无法释放。worker 启动后首次扫描直接清掉了 **39 个**历史遗留未支付订单，证明该缺口在真实数据上已发生。

## 实现

1. `expire_overdue_reservations` 扫描 `status=reserved` 且 `expires_at < now` 的订单 ID；逐单调 `expire_order`。
2. `expire_order` 行锁订单，仅处理仍为 `pending_payment` 且未支付的订单；回补库存、把支付单置 `failed`、订单置 `cancelled`、预留置 `expired`，写 `order_expired` 审计。
3. 同一事务写顾客站内通知「订单已超时关闭」；若顾客已设置通知邮箱，按现有 outbox 规则发邮件。
4. worker 启动时立即执行一次，之后按间隔执行；`ORDER_EXPIRE_INTERVAL_SECONDS<=0` 可关闭调度。
5. 手工脚本复用同一服务，避免逻辑分叉。

## 验证结果

| 项 | 结果 |
|---|---|
| 后端测试 | `tests/test_order_expiry.py`：一个过期订单 + 一个未过期订单，扫描只取消过期单，库存从 13/2 回补为 14/1，预留置 `expired`，顾客收到 `order_expired` 通知 |
| 后端全量 | `uv run pytest -q`：**140 passed**（当时；后续新增地址簿测试，最终全量 144）；`ruff` 通过 |
| 任务评测 | `uv run python -m app.scripts.task_eval`：15/15 通过 |
| 审计链 | `uv run python -m app.scripts.verify_audit`：OK |
| worker 日志 | 启动：`expired 39 unpaid orders`；下一周期：`expired 1 unpaid orders` |
| 实机探测 | 订单 `ORD-20261103-7F966550` 预留置过期后，≤60 秒变为 `cancelled`；站内通知「订单已超时关闭」，正文含订单号与“库存已释放” |

## 边界

- 调度粒度为 60 秒，不是精确到期瞬间；预留有效期本身是 15 分钟。
- 以数据库 `expires_at` 为准，依赖 worker 存活；worker 停止期间到期订单会在恢复后集中清理。
- 与支付并发时由订单行锁串行化：已支付订单不会被超时取消。
- 通知为同事务站内信；邮件走 outbox 异步投递，可能重复。
- 本机 39 个历史订单来自开发/评测数据，不能当作生产订单量。
