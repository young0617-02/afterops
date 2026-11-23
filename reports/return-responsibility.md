# 退货质检判责与质检报告

- 验证时间：2026-11-10
- 范围：退货确认收货时可按明细填写责任判定（商家/顾客/物流/其他），后台展示责任分布并导出质检报告 CSV；商家责任禁止质检扣减
- 迁移：`d8f0a2c4e6b9`（`return_items.responsibility`）

## 行为

1. 退货明细新增可空责任字段，取值 `merchant`/`customer`/`logistics`/`other`；未填写视为「未判定」。
2. `POST /api/admin/returns/{id}/receive` 的 `items[]` 新增可选 `responsibility`；与处置、逐件扣减一起保存并写审计 `return_received.responsibilities`。
3. 校验：责任值必须合法；责任为商家时该明细禁止质检扣减（422），避免一边判商家责任一边扣顾客款。
4. 退货视图与顾客售后进度展示逐件责任；后台收货后明细行展示责任标签，收货表单逐件提供责任下拉框。
5. 售后分析新增责任分布：按责任统计明细项数、件数与扣减金额，后台分析面板展示五类责任卡片。
6. 新增 `GET /api/admin/returns/inspection-report.csv?days=30`：按质检时间导出退货单号、订单号、顾客手机、SKU、商品、数量、处置、责任、扣减金额、质检说明、质检人与质检时间；仅店主可下载。
7. 报告 CSV 对以 `=`、`+`、`-`、`@`、Tab、CR 开头的文本单元格加前导单引号，避免 Excel 公式注入。
8. 售后分析面板提供「导出质检报告 CSV」，默认跟随当前 7/30/90 天窗口。

## 验证结果

| 项 | 结果 |
|---|---|
| 后端测试 | `tests/test_return_responsibility.py`：顾客责任保存/视图/分析/CSV；商家责任扣减 422、非法责任 422；CSV 公式注入转义 |
| 后端全量 | `.venv/Scripts/python.exe -m pytest -q`：**326 passed**；`.venv/Scripts/ruff check app tests` 通过 |
| 前端检查 | `npm run typecheck` 通过；Docker `npm run build` 随 web 重建通过 |
| 前端 E2E | 退货退款用例增加顾客责任选择、后台/顾客展示与质检报告下载内容校验；全量 **41 passed** |
| 迁移 | 真实 PostgreSQL `upgrade head` → `downgrade -1` → `upgrade head` 通过 |
| 任务评测 | `python -m app.scripts.task_eval`：19/19 通过 |
| 审计链 | `python -m app.scripts.verify_audit`：merchant=5 OK |
| 容器 | api/web/worker 已重建；10 个服务 Up，api/postgres/mailpit 为 healthy |

## 边界

- 责任按退货明细记录，不支持整单责任或一个明细多责任；未判定保留为空。
- 责任来自人工选择，没有根据原因/照片/物流自动推断，也不触发自动退款或扣减。
- 商家责任只禁止逐件扣减；整单扣减无法绑定责任，仍按原有规则处理。
- 质检报告只含已质检明细，默认 30 天、最多 365 天；无报告导出审计、定时邮件或权限细分。
- CSV 仅做公式前缀转义，不提供 PDF/Excel 排版、签名或质检报告编号。
- 顾客只看到责任标签，不展示质检人邮箱、内部导出字段或其他顾客数据。
