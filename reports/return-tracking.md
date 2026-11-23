# 退货运回物流

- 验证时间：2026-11-05
- 范围：顾客登记回寄单号后的物流事件时间线，店主逐节点推进
- 迁移：`c4e6f8a0d2b5`（`return_requests.shipment_status`、`return_tracking_events`）

## 行为

1. 顾客填写承运商与退货运单号后，退货状态进入 `in_transit`，生成「已登记回寄」事件；登记后单号锁定，不能再次修改。
2. 新增 `POST /api/admin/returns/{id}/tracking/advance`，仅店主可用；严格按 `registered → picked_up → in_transit → out_for_delivery → delivered` 逐节点推进，跳级或回退返回 409。
3. 每个节点新增 `return_tracking_events` 事件（状态、说明、操作人、时间）并写 `return_tracking_advanced` 审计，同时通知顾客「退货物流更新」。
4. 未登记回寄单号不能推进；已解决/已收货等非 `approved/in_transit` 状态不能推进；未知状态 422，工单不存在 404。
5. 到达退货仓只表示物流到仓，不自动完成退款/换货；店主仍需确认收货并按件回补/报损。
6. 顾客售后进度与后台退货卡片均展示物流时间线；后台为店主提供「推进物流：下一节点」按钮，客服只读。
7. 退货列表返回 `shipment_status` 与 `tracking_events`；沿用现有退货筛选与详情。

## 验证结果

| 项 | 结果 |
|---|---|
| 后端测试 | `tests/test_return_tracking.py`：登记生成首事件与单号锁定、跳级 409、逐节点推进、到仓后不能继续推进、顾客视图五条事件、到仓后仍可确认收货、审计字段；无单号推进 409、客服推进 403、非法状态 422、缺失工单 404 |
| 后端全量 | `uv run pytest -q`：**264 passed**；`ruff check app tests` 通过 |
| 前端检查 | `npm run typecheck`、Docker `npm run build` 通过 |
| 前端 E2E | `npm run test:e2e`：**33 passed**；多商品退货用例扩展为「登记回寄 → 逐节点推进到退货仓 → 报损收货 → 顾客查看完整时间线」 |
| 迁移 | 真实 PostgreSQL `upgrade head` → `downgrade -1` → `upgrade head` 通过 |
| 任务评测 | `uv run python -m app.scripts.task_eval`：19/19 通过 |
| 审计链 | `uv run python -m app.scripts.verify_audit`：merchant=5 OK |
| 容器 | api/web/worker 已重建；10 个服务 Up，api/postgres/mailpit 为 healthy |

## 边界

- 物流节点由店主手动推进，不接承运商 API/webhook，也没有自动扫描或轨迹同步。
- 只有五个正向节点；没有异常件、退回寄件人、丢失、改址、预计送达或物流备注。
- 顾客登记单号后不可修改；没有退货面单生成或上门取件。
- 到仓不触发自动验收/退款；报损与回补仍需人工确认收货。
- 事件说明由后端默认文案生成，后台 UI 不提供自定义说明（API 支持 `description`）。
- 没有物流事件导出、超时提醒或按承运商统计。
