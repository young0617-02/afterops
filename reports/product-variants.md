# 商品规格分组

- 验证时间：2026-11-05
- 范围：同款商品多规格以独立 SKU 维护，商品详情可切换规格
- 迁移：`e1a3c5d7f9b2`（`products.variant_group / variant_label`）

## 行为

1. 商品新增 `variant_group`（规格组）与 `variant_label`（规格名称），创建、编辑与 CSV 导入均可填写，长度分别限制 64/80 字符。
2. 同一 `variant_group` 下可有多个独立 SKU；每个 SKU 保持自己的价格、库存、成本、保修、图片与上下架状态。
3. 商品详情接口新增 `variants`：返回同组全部在售 SKU 的规格名、价格、可售数量与主图；规格组为空时返回空列表。
4. 商品详情页展示规格选择器，选中项高亮；点击其他规格跳转到对应 SKU 详情，标题、价格与库存随之切换。
5. 店面商品列表与后台商品卡显示规格名称；店面搜索与后台商品搜索均可匹配规格组/规格名称。
6. 购物车、订单、库存、评价、退货、换货仍按 SKU 独立处理，不需要修改既有交易链路。
7. 后台创建/编辑商品支持规格组/名称；CSV 可选 `variant_group`、`variant_label` 列。

## 验证结果

| 项 | 结果 |
|---|---|
| 后端测试 | `tests/test_product_variants.py`：同组两 SKU 详情返回 variants、规格价格/库存独立、按规格名搜索、后台字段与更新审计、空组不返回 variants、超长组 422；`tests/test_product_import.py` 覆盖 CSV 规格列 |
| 后端全量 | `uv run pytest -q`：**276 passed**；`ruff check app tests` 通过 |
| 前端检查 | `npm run typecheck`、Docker `npm run build` 通过 |
| 前端 E2E | `npm run test:e2e`：**36 passed**；新增「顾客在商品详情切换同组规格，标题与价格更新」 |
| 迁移 | 真实 PostgreSQL `upgrade head` → `downgrade -1` → `upgrade head` 通过 |
| 任务评测 | `uv run python -m app.scripts.task_eval`：19/19 通过 |
| 审计链 | `uv run python -m app.scripts.verify_audit`：merchant=5 OK |
| 容器 | api/web/worker 已重建；10 个服务 Up，api/postgres/mailpit 为 healthy |

## 边界

- 这是「规格分组 + 独立 SKU」，不是统一变体矩阵：没有选项维表（颜色×容量）、自动生成组合 SKU、共享库存或统一价格策略。
- 规格组/名称是自由文本，不做同组类目/价格一致性校验；同名组可能包含不同类目商品。
- 店面商品列表仍逐 SKU 展示，未按组聚合为一张卡；只有详情页做同组切换。
- 每个规格独立维护图片/描述/规格参数，不能继承组级公共内容。
- 修改规格组只影响导航归属，不迁移库存、订单或评价数据。
- 没有规格维度销量/库存聚合报表或变体级搜索筛选。
