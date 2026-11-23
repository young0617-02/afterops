# 订单履约与物流轨迹

- 验证时间：2026-11-03（补充自动完成）
- 范围：已支付订单的运单创建、物流节点推进、客户轨迹展示、权限与审计
- 入口：`/admin` → 订单履约；顾客端 `/orders` 订单详情
- 迁移：`ac7cb534343a`（同一商家内运单号唯一）

## 状态机

| 当前 | 允许推进 |
|---|---|
| 待揽收 `pending` | 运输中 / 异常件 |
| 运输中 `in_transit` | 派送中 / 异常件 |
| 派送中 `out_for_delivery` | 已签收 / 异常件 |
| 异常件 `exception` | 运输中 / 派送中 / 已签收 |
| 已签收 `delivered` | 终态，不可再推进 |

- 创建运单要求订单已支付且未履约；已退款/未支付订单拒绝创建。
- 创建后订单进入 `fulfilling / allocated`；离开待揽收后进入 `shipped / shipped`；签收后进入 `delivered / delivered`；顾客确认收货后进入 `completed`。
- Worker 每小时扫描超过 `ORDER_AUTO_COMPLETE_DAYS`（默认 7 天）的已签收订单，自动置 `completed` 并写审计；有进行中退货或待审提案的订单跳过。
- 每次创建与推进写入 `shipment_events` 和哈希链审计事件（`shipment_created` / `shipment_status_changed`）。

## 验证结果

| 项 | 结果 |
|---|---|
| 后端测试 | `uv run pytest -q`：**119 passed**（含履约全流程、重复/非法推进、agent 只读、退款单拒发货、确认收货、自动完成与跳过守卫） |
| 任务评测 | `uv run python -m app.scripts.task_eval`：**14/14 通过** |
| 前端 E2E | `npm run test:e2e`：**10 passed**（购买 → 创建运单 → 运输 → 派送 → 签收 → 客户轨迹、确认收货；agent 履约只读） |
| 浏览器实测 | msedge 1440×1000：后台创建运单、推进节点、客户订单页物流轨迹均可见 |
| 审计链 | 履约测试内 `verify_chain` = `(True, "ok")` |
| 唯一约束 | 同一商家重复运单号返回 409；数据库迁移已应用 |
| AI 只读联动 | 签收后聊天查询该订单，回复包含运单号与「已签收」，意图为 `LOGISTICS_TRACK` |

## 接口

- `GET /api/admin/orders?open_only=true|false&limit=50`：客服/店主
- `POST /api/admin/orders/{order_no}/shipment`：店主
- `POST /api/admin/orders/{order_no}/shipment/advance`：店主
- `GET /api/store/orders/{order_no}`：顾客（返回 `shipment.events`）

## 边界

- 物流节点由商家手工录入，未接承运商查询/签收回调；不是真实物流。
- 一单一运单，不支持拆包/多次发货；无退货物流与逆向履约。
- 签收不会立即完成；顾客可手动确认，worker 在超期后自动完成，但仍不触发资金结算（当前无结算）。
- 数据来自 dev 环境，不构成容量或生产 SLA 结论。
