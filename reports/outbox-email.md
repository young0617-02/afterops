# Outbox worker 与客服邮件

> 后续更新：outbox 已在客服邮件之外增加顾客邮件通知（`customer_notification_email`），见 [customer-email.md](customer-email.md)。本页验证数字与边界为当时快照。

- 验证时间：2026-11-03（后续补充失败监控/重试）
- 范围：新提案/新人工工单 → 事务内写 outbox → 独立 worker → SMTP 邮件
- 投递目标：本机 Mailpit（`mailpit:1025`），收件人为启用客服账号

## 流程

1. `notify_staff` 在业务事务内写站内通知，并为每个启用客服写一条 `outbox` 事件（`staff_notification_email`）。
2. 独立 `worker` 容器轮询 `outbox`，按 `available_at` 取待处理事件，通过 SMTP 发送。
3. 成功置 `processed`；失败递增 `attempts` 并按退避重试；达到 `OUTBOX_MAX_ATTEMPTS` 置 `failed`。
4. 邮件正文包含通知正文和 `ADMIN_BASE_URL/admin` 入口。
5. 失败原因写入 `outbox.last_error`；后台「邮件投递」可按状态查看并重新排队，重试动作写审计。

## 验证结果

| 项 | 结果 |
|---|---|
| Worker 启动 | `outbox worker started smtp=mailpit:1025 poll=5.0s` |
| 真实投递 | 任务评测触发的提案/工单产生 **12 封**邮件，worker 全部 `processed`；Mailpit 收到 `[AfterOps] 新的退款提案 #141`、`[AfterOps] 新的转人工工单 TK-000028` 等 |
| 重试 | 模拟 SMTP `OSError`：首次 `pending` + 退避；达到上限后 `failed`，记录 `last_error` |
| 幂等 | 已 `processed` 的事件不再发送 |
| 后端测试 | `tests/test_outbox_worker.py`：入队、收件人、发送成功、失败退避/终止 |
| 后端全量 | `uv run pytest -q`：**112 passed**；`ruff` 通过 |
| 任务评测 | `uv run python -m app.scripts.task_eval`：14/14 通过 |
| 前端 E2E | `npm run test:e2e`：10 passed |

## 边界

- 投递为 **at-least-once**：SMTP 调用无法与数据库事务原子提交，发送后进程崩溃可能重发。
- 仅客服邮件，没有顾客邮件/短信/IM/Web Push；邮件目标是本地 Mailpit，不是生产 SMTP。
- 已有失败列表与手动重试；没有自动死信修复或批量重放。
- 单 worker 消费；未做多 worker 的 `SKIP LOCKED` 调度与吞吐测试；手动重试保留最近错误，成功后清空。
- 邮件正文未做额外脱敏；当前载荷含订单号/金额，不含手机号/地址。
