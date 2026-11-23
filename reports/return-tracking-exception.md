# 退货物流异常登记

- 验证时间：2026-11-05
- 范围：退货运回途中登记物流异常，顾客时间线可见，后续节点可继续推进
- 迁移：`b8d0f2a4c6e9`（`return_tracking_events.status` 增加 `exception`）

## 行为

1. 物流事件新增 `exception` 类型；店主可对已登记回寄单号、状态为 `approved/in_transit` 的退货填写异常说明（必填，≤500 字）。
2. 异常作为一条时间线事件写入 `return_tracking_events`，不改变 `shipment_status`，也不改变退货主状态；后续「已揽收→运输中→派送中→已到达退货仓」仍可正常推进。
3. 登记写 `return_tracking_exception` 审计（退货、订单、承运商、运单号、当前物流状态、说明、操作人），并通知顾客「退货物流异常」。
4. 未登记回寄单号、已收货/已驳回等状态不能登记异常；客服账号只读，登记需店主权限。
5. 后台退货卡提供「物流异常说明」输入与「登记物流异常」按钮；异常记录显示在后台与顾客的物流时间线中，标签为「物流异常」。
6. 同一退货可多次登记异常（例如破损后又丢失），每次独立事件与审计；没有异常关闭/解决状态。

## 验证结果

| 项 | 结果 |
|---|---|
| 后端测试 | `tests/test_return_tracking.py`：未登记单号 409、空说明 422、登记后事件与标签、`shipment_status` 不变、审计与顾客通知、异常后仍可推进到已揽收、客服 403 |
| 后端全量 | `uv run pytest -q`：**270 passed**；`ruff check app tests` 通过 |
| 前端检查 | `npm run typecheck`、Docker `npm run build` 通过 |
| 前端 E2E | `npm run test:e2e`：**35 passed**；多商品退货用例增加「登记异常 → 继续推进到仓 → 顾客时间线显示异常」 |
| 迁移 | 真实 PostgreSQL `upgrade head` → `downgrade -1` → `upgrade head` 通过 |
| 任务评测 | `uv run python -m app.scripts.task_eval`：19/19 通过 |
| 审计链 | `uv run python -m app.scripts.verify_audit`：merchant=5 OK |
| 容器 | api/web/worker 已重建；10 个服务 Up，api/postgres/mailpit 为 healthy |

## 边界

- 异常只是事件记录，不是终态；没有异常类型枚举、异常关闭/恢复状态或异常时效统计。
- 不自动重派、退回寄件人、理赔或生成补偿提案；店主仍需按原流程推进或人工收货。
- 顾客只能查看异常说明，不能回复或上传异常凭证；凭证仍走退货申请附件。
- 同一退货可重复登记异常，没有去重或异常次数限制。
- 不影响退款金额、库存回补/报损或换货逻辑。
- 无承运商 API/webhook 自动异常识别；全部由店主手动登记。
