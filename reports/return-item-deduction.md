# 退货逐件质检扣减

- 验证时间：2026-11-09
- 范围：退货确认收货时可按退货明细分别填写质检扣减金额，退款按逐件扣减合计计算；保留整单扣减兼容入口
- 迁移：`b1d3f5a7c9e2`（`return_items.inspection_deduction_cents`）

## 行为

1. `ReturnItem` 保存逐件质检扣减金额；退货单级 `inspection_deduction_cents` 作为合计值，保持既有视图与通知口径。
2. `POST /api/admin/returns/{id}/receive` 的 `items[]` 新增可选 `deduction_cents`；有逐件扣减时退款金额 = 可退金额 − 逐件合计。
3. 兼容旧调用：仍可只传整单 `deduction_cents`；同一请求不能同时使用整单与逐件扣减（422）。
4. 逐件扣减与质检处置（回补/报损）独立；无论回补还是报损都可填写扣减，扣减影响退款金额，不改变回补数量。
5. 校验：逐件扣减必须是非负整数金额、明细必须属于该退货单、合计不能超过扣除运费后的可退金额；有任一扣减必须填写原因。
6. 审计 `return_received` 增加 `item_deductions` 明细（退货明细 ID、SKU、数量、金额）；退款提案金额使用扣减后金额。
7. 后台退货卡逐行提供「扣减(元)」输入与共享原因，收货后展示逐件扣减；顾客售后进度展示总扣减与逐件扣减明细。
8. 换货仍不支持质检扣减；整单扣减与逐件扣减的提示、错误消息区分。

## 验证结果

| 项 | 结果 |
|---|---|
| 后端测试 | `tests/test_inspection_deduction.py`：两件分别扣减 50/20 → 合计 70、退款 1028.00、退货视图与审计明细正确；缺原因 422、整单+逐件混用 422、超额 409 |
| 后端全量 | `.venv/Scripts/python.exe -m pytest -q`：**306 passed**；`ruff check app tests` 通过 |
| 前端检查 | `npm run typecheck`、Docker `npm run build` 通过 |
| 前端 E2E | `npx playwright test`：**39 passed**；退货收货用例改为逐件扣减 10 元并验证退款/顾客查看 |
| 迁移 | 真实 PostgreSQL `upgrade head` → `downgrade -1` → `upgrade head` 通过 |
| 任务评测 | `python -m app.scripts.task_eval`：19/19 通过 |
| 审计链 | `python -m app.scripts.verify_audit`：merchant=5 OK |
| 容器 | api/web/worker 已重建；10 个服务 Up，api/postgres/mailpit 为 healthy |

## 边界

- 仍是整单一次收货；不支持逐件分别收货或逐件生成不同退款提案。
- 扣减只记金额与共享原因，不支持逐件独立原因/照片绑定。
- 没有扣减上限规则、自动按商品价格比例校验、争议申诉与质检报告导出。
- 退款仍走 mock PSP 账本；扣减不影响库存回补/报损或发票自动冲减。
- 旧整单扣减接口继续保留，两个口径不能在同一请求混用。
