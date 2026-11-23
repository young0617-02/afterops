# 换货补发物流闭环

- 验证时间：2026-11-09
- 范围：换货替换订单生成后，补发包裹的运单、状态与轨迹进入退货视图；顾客售后页、后台退货卡与 AI 回复均可查看
- 迁移：无（复用 `shipments` / `shipment_events` 与 `return_requests.replacement_order_id`）

## 行为

1. 退货列表（顾客 `/api/store/returns`、后台 `/api/admin/returns`）在 `replacement_order_no` 之外返回 `replacement_shipments[]`，包含包裹序号、承运商、运单号、状态、当前节点、发货/签收时间与事件轨迹；批量查询订单与事件，避免逐条 N+1。
2. 换货收货后补发订单未发货时 `replacement_shipments` 为空；后台按现有履约流程为替换订单创建运单、推进状态后，顾客售后进度自动展示补发包裹与时间线。
3. 替换订单发货与状态推进仍走 `/api/admin/orders/{order_no}/shipment` 与 `.../advance`，不新增发货入口；多包裹能力同样适用于替换订单。
4. 发货通知区分场景：替换订单创建包裹/推进状态时标题为「换货补发包裹已创建」「换货补发运输中」等，正文标明换货订单号，跳转 `/after-sales`；普通订单保持原文案与 `/orders` 跳转。
5. 后台退货卡展示每个补发包裹的承运商、运单号、当前节点与轨迹；顾客售后卡展示补发包裹与轨迹，收货状态文案区分「等待发货」与「已补发」。
6. AI `get_returns` 返回 `replacement_order_no` 与 `replacement_shipments[]`，退货进度回复展示「换货订单 + 补发包裹/运单/节点」，数据来自系统事实并保留 `return:` 引用。

## 验证结果

| 项 | 结果 |
|---|---|
| 后端测试 | `tests/test_returns_api.py::test_exchange_replacement_shipment_visible_in_returns`：换货收货 → 替换订单创建运单并推进 → 顾客/后台列表均返回补发包裹与事件；补发通知标题/正文/跳转正确 |
| 后端测试 | `tests/test_chat_after_sales_facts.py::test_chat_exchange_progress_includes_replacement_shipment`：聊天退货进度含换货订单号、补发运单与节点，引用为 `return:` |
| 后端全量 | `.venv/Scripts/python.exe -m pytest -q`：**308 passed**；`ruff check app tests` 通过 |
| 前端检查 | `npm run typecheck` 通过；Docker `npm run build` 随 web 重建通过 |
| 前端 E2E | 多商品换货用例扩展：收货后为替换订单创建京东物流运单并推进运输中，顾客售后卡显示「换货补发」与运单，后台收货筛选卡片显示补发包裹；全量 E2E **39 passed** |
| 任务评测 | `python -m app.scripts.task_eval`：19/19 通过 |
| 审计链 | `python -m app.scripts.verify_audit`：merchant=5 OK |
| 容器 | api/web/worker 已重建；10 个服务 Up，api/postgres/mailpit 为 healthy |

## 边界

- 补发物流只读系统内运单与事件，不接承运商实时轨迹 API；节点仍由后台手工推进。
- 不校验替换订单发货仓与退货回补仓是否一致，不做补发时效 SLA。
- 不自动在换货收货时创建运单，仍需履约人员选择承运商与运单号。
- 通知为站内信 + 既有邮件 outbox 路径，不接短信/微信。
- 顾客端只展示补发包裹，不提供改地址、拦截或合并包裹操作。
