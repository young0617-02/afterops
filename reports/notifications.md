# 站内通知

> 后续更新：顾客通知已支持同步邮件投递，见 [customer-email.md](customer-email.md)。本页验证数字为当时快照。

- 验证时间：2026-11-02
- 范围：顾客/客服站内通知、未读数、逐条与全部已读
- 迁移：`79e9728ffb7e`（`notifications`）

## 触发点

| 事件 | 接收人 | 类型 |
|---|---|---|
| AI 创建退款/补偿/改地址提案 | 全部启用客服账号 | `proposal_created` |
| 提案执行 | 提案所属顾客 | `proposal_executed` |
| 提案驳回 | 提案所属顾客 | `proposal_rejected` |
| 创建人工工单 | 全部启用客服账号 | `handoff_created` |
| 人工工单解决 | 工单所属顾客 | `handoff_resolved` |
| 创建运单 | 订单所属顾客 | `shipment_created` |
| 运输/派送/签收/异常 | 订单所属顾客 | `shipment_updated` |

## 接口与页面

- `GET /api/store/notifications`、`POST /api/store/notifications/{id}/read`、`POST /api/store/notifications/read-all`
- `GET /api/admin/notifications`、`POST /api/admin/notifications/{id}/read`、`POST /api/admin/notifications/read-all`
- 顾客顶部铃铛（数量徽标、下拉列表、跳转售后/订单）；商家后台右上铃铛（跳转后台）。
- 打开下拉、页面路由变化、每 30 秒轮询时刷新；通知与业务事件同一事务写入。

## 验证结果

| 项 | 结果 |
|---|---|
| 后端测试 | `tests/test_notifications_api.py`：提案创建/执行/驳回、工单解决、运单四个节点、未读数、逐条/全部已读、顾客与客服隔离、他人 404 |
| 后端全量 | `uv run pytest -q`：**108 passed**；`ruff` 通过 |
| 前端 E2E | `npm run test:e2e`：**8 passed**；退款执行后顾客铃铛出现未读并在下拉中看到「退款已执行」；工单解决后看到「人工工单已解决」 |
| 任务评测 | `uv run python -m app.scripts.task_eval`：11/11 通过 |
| 浏览器实测 | msedge 1440×1000：铃铛未读数、通知列表、订单物流四条通知均正常；修复了面板内「全部已读」按钮在深色头部继承白字的问题 |

## 边界

- 站内通知轮询间隔 30 秒，不是实时推送；客服新提案/新工单另走 outbox → Mailpit 邮件（见 [outbox-email.md](outbox-email.md)）；超时提案/工单另发一次提醒（见 [sla.md](sla.md)）；无顾客邮件/短信/IM/Web Push。
- 客服通知按当前启用账号逐人落库；新增账号不会收到历史通知。
- 无通知偏好、免打扰、分组或清理策略；列表最多取 20 条。
- 通知正文包含订单号、金额、运单号等业务信息，未做脱敏导出。
