# 退货运费扣除

- 验证时间：2026-11-04
- 范围：店主配置固定退货运费；整单/部分退款扣除运费；无需退款场景；顾客与帮助中心展示
- 迁移：`6235d4f181b6`（政策运费字段、退货单最终退款金额）

## 行为

1. 退货政策新增「退货运费（元，从退款中扣除）」，范围 0–200 元，默认 0。
2. 退货资格接口返回 `return_shipping_fee_cents`；顾客在可退时看到「退款将扣除退货运费 X 元」。
3. 确认收货时：
   - 整单退回：应退金额 = 剩余可退金额 − 退货运费；
   - 部分退回：应退金额 = 所选商品小计 − 订单折扣分摊 − 退货运费；
   - 运费 ≥ 应退金额：不生成退款提案，退货直接置为已收货，通知顾客「扣除退货运费后无需退款」。
4. 换货不扣除退货运费；换货差价退款仍走原逻辑。
5. 最终退款金额持久化在 `return_requests.refund_amount_cents`，顾客与后台退货卡片显示退款金额。
6. `return_received` 审计记录 `return_shipping_fee_cents`、`refund_amount_cents`、`refund_scope` 与退款提案 ID（可能为空）。
7. 帮助中心退货摘要新增「退货运费」卡片。
8. 政策保存按钮在政策加载完成前禁用，避免网络慢时用默认值覆盖已保存政策。

## API

- `GET/PATCH /api/admin/return-policy` 新增 `return_shipping_fee_cents`。
- `GET /api/store/returns/eligibility` 返回该字段。
- 退货单响应新增 `refund_amount_cents`。
- `GET /api/store/help` 的 `return_policy` 新增该字段。

## 验证结果

| 项 | 结果 |
|---|---|
| 后端测试 | `tests/test_return_fee.py`：整单扣运费、部分退货扣运费、运费高于退款不建提案、金额持久化、审计字段、0–200 元边界 |
| 后端全量 | `uv run pytest -q`：**189 passed**；`ruff check app tests` 通过 |
| 前端检查 | `npm run typecheck`、Docker `npm run build` 通过 |
| 前端 E2E | `npm run test:e2e`：**24 passed**；店主设置 12 元运费后顾客看到扣费提示，测试结束恢复 0 元 |
| 任务评测 | `uv run python -m app.scripts.task_eval`：19/19 通过（默认运费 0，行为不变） |
| 实机退款核算 | 真实 API/PostgreSQL：订单 899.00 元、运费 12.00 元 → 退货 `#220` 退款 **887.00 元**，退款提案 `#824`，状态 `received`；探针结束恢复运费 0 |
| 实机政策审计 | `return_policy_updated` 记录 `return_shipping_fee_cents: 1200`，测试结束恢复 0 |
| 审计链 | `uv run python -m app.scripts.verify_audit`：merchant=5 OK |

## 边界

- 运费是每笔退货的固定金额，不按商品、件数、距离、承运商或责任方计算。
- 运费只在退款中扣除；换货不收费，也不生成独立的运费收款单。
- 运费高于应退金额时直接无需退款，不生成 0 元提案，也没有“顾客补运费”流程。
- 不支持运费险、退货包运费、按顾客/会员豁免或单笔手工减免。
- 退款审批被驳回后运费不会单独记账；财务对账仍只看退款提案/账本。
- 部分退款运费只扣一次；多次部分退货会按每次退货分别扣固定运费。
- 顾客在提交前只能看到固定提示，最终金额以收货核算为准。
