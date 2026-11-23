# 退货质检处置

- 验证时间：2026-11-05
- 范围：确认收货时按退货明细选择「回补库存 / 报损不入库」，报损件不增加可售库存
- 迁移：`f1b3d5e7a9c2`（`return_items.disposition / inspected_by / inspected_at`）

## 行为

1. `ReturnItem` 新增质检处置、质检人与质检时间；迁移前存量退货保持 `NULL`，确认收货时再落值。
2. `POST /api/admin/returns/{id}/receive` 新增 `items: [{item_id, disposition}]`；`disposition` 仅允许 `restock` / `damaged`，未知明细返回 422，重复明细在请求模型层拒绝。
3. 未传 `items` 时兼容旧行为：`restock=true` 全部回补、`restock=false` 全部视为报损；无明细的旧整单退货仍按原逻辑处理。
4. 仅 `restock` 明细写 `return_restocked` 库存流水并增加可售库存；`damaged` 明细不写库存流水、不增加库存，但保存处置结果。
5. 退货详情与顾客售后进度返回每条明细的 `disposition`；已收货卡片显示「已回补 / 已报损」，后台确认提示同时给出回补与报损件数。
6. `return_received` 审计新增 `damaged_items`（SKU/数量）；回补与退款/换货逻辑保持原状，报损不改变退款金额。
7. 质检人取当前确认收货的店主账号；客服仍只能查看退货进度。

## 验证结果

| 项 | 结果 |
|---|---|
| 后端测试 | `tests/test_partial_returns.py`：混合回补/报损只写一条回补流水、报损件库存不变、明细处置/质检人/时间落库、审计 `damaged_items`、顾客接口可见；未知明细/重复明细/非法处置 422；全部报损时无流水且退款提案仍生成 |
| 后端全量 | `uv run pytest -q`：**258 passed**；`ruff check app tests` 通过 |
| 前端检查 | `npm run typecheck`、Docker `npm run build` 通过 |
| 前端 E2E | `npm run test:e2e`：**33 passed**；退货主流程改用逐件处置选择，多商品退货用例完成「只退一件 → 选择报损 → 收货 → 后台/顾客显示已报损」 |
| 迁移 | 真实 PostgreSQL `upgrade head` → `downgrade -1` → `upgrade head` 通过 |
| 任务评测 | `uv run python -m app.scripts.task_eval`：19/19 通过 |
| 审计链 | `uv run python -m app.scripts.verify_audit`：merchant=5 OK |
| 容器 | api/web/worker 已重建；10 个服务 Up，api/postgres/mailpit 为 healthy |

## 边界

- 处置粒度是整条退货明细，不支持同一 SKU 部分回补、部分报损；同一订单多数量需拆分为多条明细才能拆分处置。
- 没有残次品独立仓、报废出库单、损耗金额与成本核算；报损只记录数量与处置结果。
- 不支持质检照片、序列号核验、自动判责或按责任方扣款；图片凭证仍走退货申请附件。
- 报损不影响退款金额；退款仍按所选商品小计、折扣分摊与退货运费规则计算。
- 确认收货后不可修改处置结果；没有质检复核/撤销流程。
- 旧整单退货（无明细）仍只能整体选择回补或全部不补，不会生成逐件报损记录。
