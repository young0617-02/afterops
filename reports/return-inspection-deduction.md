# 退货质检扣减

- 验证时间：2026-11-09
- 范围：店主确认退货收货时，可按质检结果填写扣减金额与原因，退款提案按扣减后金额生成，顾客可看到依据
- 迁移：`c5e7a9b1d3f6`（`return_requests.inspection_deduction_cents`、`inspection_note`）

## 行为

1. `POST /api/admin/returns/{id}/receive` 新增可选 `deduction_cents` 与 `inspection_note`；不传时保持原金额，兼容既有调用。
2. 扣减基于扣除退货运费后的可退金额计算；扣减不能超过该金额，超过返回 409；扣减大于 0 时必须填写原因，否则 422。
3. 最终退款金额 = 可退金额 − 质检扣减；扣减到 0 时不生成退款提案，退货仍进入 `received` 并通知顾客「质检扣减后无需退款」。
4. 退款提案备注包含「退货验收：原因；质检扣减 X 元：说明」，提案仍由店主批准后执行；不绕过审批。
5. 退货单保存扣减金额与质检说明，审计 `return_received` 记录扣减前金额、扣减金额、最终退款与说明；顾客售后进度和后台退货卡展示扣减与原因。
6. 换货不支持质检扣减；提交非零扣减或说明会返回 422，避免把退回商品的折价混入换货差价。
7. 质检处置（回补/报损）与扣减相互独立：可以先按件报损，再对缺失配件、外观损伤等扣减现金；扣减不影响回补数量。
8. 退款金额仍受订单剩余可退上限约束，累计退款不超过实付；扣减记录不改变原订单明细金额。

## 验证结果

| 项 | 结果 |
|---|---|
| 后端测试 | `tests/test_inspection_deduction.py`：899.00 退货扣减 50.00 → 提案/退款 849.00；超额扣减 409、无原因 422、扣减至 0 不生成提案并通知、换货扣减 422；审计与顾客视图字段 |
| 后端全量 | `.venv/Scripts/python.exe -m pytest -q`：**289 passed**；`ruff check app tests` 通过 |
| 前端检查 | `npm run typecheck`、Docker `npm run build` 通过 |
| 前端 E2E | `npx playwright test`：**38 passed**；退货收货用例增加「填写质检扣减 10 元与原因 → 提案金额递减 → 顾客售后进度显示扣减依据」 |
| 迁移 | 真实 PostgreSQL `upgrade head` → `downgrade -1` → `upgrade head` 通过 |
| 任务评测 | `python -m app.scripts.task_eval`：19/19 通过 |
| 审计链 | `python -m app.scripts.verify_audit`：merchant=5 OK |
| 容器 | api/web/worker 已重建；10 个服务 Up，api/postgres/mailpit 为 healthy |

## 边界

- 扣减是整单一个金额与一段说明，不支持按退货明细逐项扣减或逐件定价。
- 扣减只影响退款金额，不改变库存回补/报损数量，也不生成残次品价值账。
- 扣减需店主在确认收货时一次填写；不支持收货后追加、撤销或重新协商。
- 换货订单不适用；换货差价仍按既有提案流程处理。
- 退款仍走 mock PSP 账本，不承诺到账时间；扣减依据不生成 PDF/质检报告。
- 不支持自动扣减规则、照片自动判责、保险理赔或平台仲裁。
