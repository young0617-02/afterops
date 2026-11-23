# 订单拆包发货（多包裹）

- 验证时间：2026-11-09
- 范围：一个订单可拆成多个包裹，逐件分配商品数量，按包裹推进物流，全部包裹签收后订单才完成
- 迁移：`a3c5e7b9d1f2`（`shipments.package_no`、`shipment_items`、移除 `order_id` 唯一约束）

## 行为

1. `POST /api/admin/orders/{order_no}/shipment` 可重复调用；请求体可选 `items: [{order_item_id, qty}]`，按订单明细逐件分配。省略 `items` 时按“全部剩余未发数量”创建一个整单包裹，兼容原有调用方式。
2. 每次创建生成递增的 `package_no`；同一订单内包裹号唯一，同一商家运单号仍全局唯一。超过剩余数量返回 409，未知明细/零数量返回 422/422，全部发完后再建包裹返回 409。
3. 订单聚合状态：只创建包裹（未揽收）为 `fulfilling/allocated`；任一包裹运输/派送或部分签收为 `shipped/shipped`；全部明细分配完且全部包裹签收才变为 `delivered/delivered`。
4. `POST .../shipment/advance` 新增可选 `shipment_id`。单包裹订单可省略；多包裹订单必须指定，否则 422；包裹不属于该订单返回 404。节点流转仍为 `pending → in_transit → out_for_delivery → delivered`，异常可恢复。
5. 部分签收通知标题为「包裹已签收」，正文说明其余包裹仍在配送；全部签收才发「订单已签收」。创建包裹通知标题为「订单已创建包裹」，正文含包裹号、运单号与件数。
6. 后台履约卡展示每个包裹的运单、逐件商品、独立时间线与独立推进表单；未发完时提供「新增包裹」逐件数量输入。顾客订单详情展示多包裹列表、每包商品与轨迹，物流摘要按包裹拼接。
7. 顾客/后台订单详情返回 `shipments[]`（含 `package_no`、`items`、`events`）；AI 物流工具返回 `packages[]`，多包裹回复逐包列出。
8. 存量单包裹数据在迁移时回填 `shipment_items`；无明细的历史包裹仍按整单已发处理，避免重复发货。退货/维修资格取所有已签收包裹的最晚签收时间；订单自动完成按最后一个包裹的签收时间计算。
9. 取消申请、取消审批与改地址提案仍检查是否已存在任一包裹；只要开始履约就拒绝整单取消。

## 验证结果

| 项 | 结果 |
|---|---|
| 后端测试 | `tests/test_multi_package_shipping.py`：拆两包逐件分配、部分签收不完成、全部签收完成、顾客/后台视图、超发 409、未知明细 422、零数量 422、重复运单 409、多包裹推进缺 `shipment_id` 422、错包裹 404、发完再建 409、自动完成取最后签收时间 |
| 后端全量 | `.venv/Scripts/python.exe -m pytest -q`：**281 passed**；`ruff check app tests` 通过 |
| 前端检查 | `npm run typecheck`、Docker `npm run build` 通过 |
| 前端 E2E | `npx playwright test`：**37 passed**；新增「拆两包 → 分别签收 → 订单完成 → 顾客查看两包 → 确认收货」 |
| 迁移 | 真实 PostgreSQL `upgrade head` → `downgrade -1` → `upgrade head` 通过（单包裹数据）；含多包裹数据时 downgrade 会显式拒绝并提示先处理多余包裹 |
| 任务评测 | `python -m app.scripts.task_eval`：19/19 通过 |
| 审计链 | `python -m app.scripts.verify_audit`：merchant=5 OK |
| 容器 | api/web/worker 已重建；10 个服务 Up，api/postgres/mailpit 为 healthy |

## 边界

- 包裹创建后不能移动已分配商品、合并包裹、拆分包裹或修改包裹内数量；只能继续创建新包裹。
- 不记录逐件签收时间；退货/维修窗口按订单最后一个包裹的签收时间计算，不按商品所在包裹分别计算。
- 不支持部分取消未发商品、按包裹退运费/分摊优惠、包裹级退款或包裹级改地址。
- 没有真实承运商 API/webhook、电子面单、预计送达、包裹重量/体积与运费重算；物流节点仍由店主手工推进。
- 同一订单所有包裹共用订单收货地址；不支持分仓自动拆包、智能路由与超卖保护以外的承运商选择。
- 后台列表最多展示 50 单，包裹数量随单返回；超大订单的包裹列表未分页。
