# 保修维修

- 验证时间：2026-11-04
- 范围：签收后保修期内按商品申请维修、审批、回寄、收货与完成
- 迁移：`b4fda5c9b198`（`repair_requests`）

## 行为

1. 顾客对已签收/已完成订单的单个商品申请维修，填写故障描述并可上传最多 3 张凭证。
2. 保修期按下单时的商品配置快照计算（0–120 个月，默认 12 个月），从运单签收时间起算；0 个月表示不提供保修，超出保修期拒绝并返回月数；提交前可查询 `/api/store/orders/{order_no}/repair-eligibility` 显示每件商品资格。
3. 同一订单商品不能同时存在进行中的维修申请；支持店主驳回与顾客撤回待审核申请。
4. 状态机：`requested → approved / rejected / withdrawn`，`approved → in_transit`，`approved / in_transit → received`，`received → completed`。
5. 店主批准后顾客登记回寄承运商/单号；店主确认收货后填写处理结果并完成维修。
6. 每次状态变化写审计（`repair_*`）并按顾客通知偏好发送站内/邮件通知。
7. 维修不产生退款或换货；如需赔付/退货，仍走现有提案与退货流程。
8. 后台「维修」页签支持按状态筛选，店主执行批准/驳回/收货/完成，客服只读。

## API

| 方法 | 路径 | 权限 | 说明 |
|---|---|---|---|
| GET | `/api/store/repairs` | 顾客 | 我的维修申请 |
| POST | `/api/store/repairs` | 顾客 | `{order_no, sku, issue, attachment_ids}` |
| POST | `/api/store/repairs/{id}/withdraw` | 顾客 | 撤回待审核 |
| POST | `/api/store/repairs/{id}/tracking` | 顾客 | 登记回寄 |
| GET | `/api/admin/repairs?status=` | 店主/客服 | 维修队列 |
| POST | `/api/admin/repairs/{id}/approve\|reject\|receive\|complete` | 店主 | 状态推进 |

## 验证结果

| 项 | 结果 |
|---|---|
| 后端测试 | `tests/test_repairs_api.py`：完整维修流程、重复进行中 409、未签收/错误商品/超保修期、客服只读与店主权限、审计事件 |
| 后端全量 | `uv run pytest -q`：**200 passed**；`ruff check app tests` 通过 |
| 前端检查 | `npm run typecheck`、Docker `npm run build` 通过 |
| 前端 E2E | `npm run test:e2e`：**25 passed**；下单→签收→申请维修→店主批准→顾客回寄→收货→完成，顾客看到处理结果 |
| 任务评测 | `uv run python -m app.scripts.task_eval`：19/19 通过 |
| 实机数据 | PostgreSQL 维修 `#2`：订单 `ORD-20261104-0C419662`、`SKU-NCM-001`、状态 `completed`、结果「已更换电池并回寄」 |
| 审计链 | `uv run python -m app.scripts.verify_audit`：merchant=5 OK |

## 边界

- 保修期按商品配置并为每笔订单快照，不支持按类目、订单或顾客单独设置；没有序列号/购买凭证校验，也没有独立的保修资格管理页。
- 维修只记录流程与结果，不自动创建退款、换货或替换订单；无法维修时需人工走其他流程。
- 不接物流 API、不生成寄回面单；回寄信息由顾客手工填写。
- 不管理维修备件、维修成本、工时、维修商或维修 SLA。
- 不做维修后二次质检、重修次数限制或维修历史统计。
- 附件仅图片，最多 3 张；无视频、无附件删除。
- 维修进度只通过站内通知/邮件偏好触达，无短信/IM 推送；没有顾客催办或超时提醒。
