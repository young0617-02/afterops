# 配送方式与运费

- 验证时间：2026-11-05
- 范围：店主配置配送方式与运费、包邮门槛、结算选择、订单运费
- 迁移：`3b6ff8dd76ba`（`shipping_methods`，为现有商家回填标准配送）

## 行为

1. 店主可创建配送方式：编码、名称、运费、包邮门槛（可选）、排序；可停用/启用。
2. 公开接口返回启用方式；结算页展示并默认选择 `STANDARD`（不存在时选排序最前的启用方式）。
3. 运费按商品小计判断包邮门槛；未配置任何方式时运费为 0。
4. 应付 = 商品小计 + 运费 − 优惠券；优惠券只抵扣商品金额，不抵扣运费。
5. 订单保存 `freight_cents` 与 `payable_cents`，支付单按应付金额创建；订单详情展示运费、优惠与应付。
6. 取消/超时只释放库存与优惠券，不涉及运费；全额退货退款仍按剩余可退金额（含运费），部分退货不退运费。
7. 配送方式创建/启停写审计。

## API

| 方法 | 路径 | 权限 | 说明 |
|---|---|---|---|
| GET | `/api/store/shipping-methods` | 公开 | 启用的配送方式 |
| GET | `/api/admin/shipping-methods` | 店主/客服 | 全部配送方式 |
| POST | `/api/admin/shipping-methods` | 仅店主 | 创建 |
| PATCH | `/api/admin/shipping-methods/{id}` | 仅店主 | 启停 |

`POST /api/store/checkout` 新增可选 `shipping_method_code`。

## 验证结果

| 项 | 结果 |
|---|---|
| 后端测试 | `tests/test_shipping_api.py`：默认标准 0 元、指定加急 12 元、包邮门槛、优惠券只抵商品、停用方式拦截、客服 403 |
| 后端全量 | `uv run pytest -q`：**210 passed**；`ruff check app tests` 通过 |
| 前端检查 | `npm run typecheck`、Docker `npm run build` 通过 |
| 前端 E2E | `npm run test:e2e`：**27 passed**；店主创建 12 元顺丰，顾客结算选择后订单详情显示运费 12.00 元 |
| 任务评测 | `uv run python -m app.scripts.task_eval`：19/19 通过（默认标准配送 0 元，金额不变） |
| 实机数据 | PostgreSQL 配送表：标准配送 0 元、E2E 顺丰 12 元均启用 |
| 审计链 | `uv run python -m app.scripts.verify_audit`：merchant=5 OK |

## 边界

- 运费是固定金额，不按重量、体积、距离、件数或承运商实时报价。
- 单笔订单只选一种配送方式；不支持分段配送、预约时段、自提点或运费险。
- 包邮门槛只看商品小计，不看优惠后金额或会员等级。
- 不接物流商 API，不生成面单，不承诺时效；运输节点仍由店主手工推进。
- 已支付订单修改配送方式不支持；退款时运费处理沿用退货规则。
