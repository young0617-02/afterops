# 发票重开提醒与筛选

- 验证时间：2026-11-09
- 范围：退款首次导致发票金额与净支付不一致时自动写审计并通知员工/顾客；后台发票队列增加「需重开」筛选
- 迁移：无（基于现有发票、退款与通知数据）

## 行为

1. 退款执行后检查订单最新 `requested/issued` 发票；若发票金额与退款后的净支付不一致，且退款前金额仍匹配（首次触发），写 `invoice_adjustment_required` 审计。
2. 员工收到站内通知「发票需重开 #id」，正文含订单号、发票金额与当前净支付；顾客收到「发票金额需调整」，说明需由商家处理重开。
3. 同一发票后续再次退款不会重复发提醒；作废/重开后标记消失，新发票按净支付重新生成。
4. `GET /api/admin/invoices?status=needs_adjustment` 返回所有待处理/已开票且金额不一致的发票；后台财务发票筛选新增「需重开」。
5. 顾客订单发票区已有「退款后需重开」提示，本次补充通知触达；不自动作废、不自动开新票。
6. 补偿不改变净支付，不触发发票提醒；已作废/已驳回发票不参与筛选。

## 验证结果

| 项 | 结果 |
|---|---|
| 后端测试 | `tests/test_invoice_refund_adjustment.py`：首次退款生成 1 条审计与员工/顾客通知；二次退款不重复；`needs_adjustment` 只返回不一致发票；作废后筛选为空 |
| 后端全量 | `.venv/Scripts/python.exe -m pytest -q`：**306 passed**；`ruff check app tests` 通过 |
| 前端检查 | `npm run typecheck`、Docker `npm run build` 通过 |
| 前端 E2E | `npx playwright test`：**39 passed**；发票用例增加「需重开筛选可见 → 顾客通知铃铛出现发票金额需调整」 |
| 迁移 | 无数据库迁移 |
| 任务评测 | `python -m app.scripts.task_eval`：19/19 通过 |
| 审计链 | `python -m app.scripts.verify_audit`：merchant=5 OK |
| 容器 | api/web/worker 已重建；10 个服务 Up，api/postgres/mailpit 为 healthy |

## 边界

- 只通知一次首次金额不一致；不按每次退款重复提醒。
- 不自动作废、不自动红冲或重开，仍需店主在发票队列人工处理。
- 不接税务系统，不生成红字发票/PDF，不支持部分红冲。
- 通知按现有站内通知与邮件偏好投递；未读取渠道已读状态。
