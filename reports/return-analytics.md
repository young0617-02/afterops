# 退货原因分类与售后分析

- 验证时间：2026-11-05
- 范围：退货申请原因分类、聊天自动归类、后台按原因/状态/Top SKU 统计
- 迁移：`e3a5b7c9d1f0`（`return_requests.reason_category`）

## 行为

1. 退货申请新增原因分类：`quality` 质量问题、`damaged` 运输破损、`wrong_item` 发错/少发、`size` 尺寸不合适、`not_needed` 不想要、`other` 其他；默认 `other`。
2. 顾客售后页退货表单选择分类，随原因文本一起提交；历史申请迁移后统一为 `other`。
3. 聊天退货按关键词自动归类（如「杂音/故障」→质量问题，「发错/少发」→发错少发，「破损」→运输破损，「尺寸/不合」→尺寸，「不想要」→不想要），无法匹配为其他。
4. 顾客售后列表与后台退货卡片展示原因分类标签 + 原始原因文本；后台审核仍按原流程。
5. 后台「退货」页新增可折叠「售后分析」：支持近 7/30/90 天，展示申请总数、待审核、已批准/回寄、已收货、驳回/撤回、退款总额、批准率、平均审批小时。
6. 同一面板展示各原因分类计数，以及 Top 5 退货商品（申请数、件数）。
7. 分析口径按退货申请 `created_at` 落在窗口内；退款总额取该窗口退货申请已记录的 `refund_amount_cents`；批准率 = 有 `approved_at` 的申请数 / 申请总数；平均审批时间 = `approved_at - created_at`。
8. 接口：`GET /api/admin/returns/analytics?days=7|30|90`，店主与客服均可读取。

## 验证结果

| 项 | 结果 |
|---|---|
| 后端测试 | `tests/test_return_analytics.py`：关键词分类 6 类；两笔不同原因退货（一笔批准）后总数/状态/原因计数、Top SKU、批准率 0.5、平均审批时间、窗口参数 422、客服可读；`tests/test_returns_api.py` 等继续通过 |
| 后端全量 | `uv run pytest -q`：**237 passed**；`ruff check app tests` 通过 |
| 前端检查 | `npm run typecheck`、Docker `npm run build` 通过 |
| 前端 E2E | `npm run test:e2e`：**31 passed**；退货用例选择「质量问题」，后台卡片显示分类，展开「售后分析」看到原因统计 |
| 迁移 | 真实 PostgreSQL `upgrade head` → `downgrade -1` → `upgrade head` 通过 |
| 任务评测 | `uv run python -m app.scripts.task_eval`：19/19 通过 |
| 审计链 | `uv run python -m app.scripts.verify_audit`：merchant=5 OK |
| 容器 | Docker Desktop 重启后整套 Compose 重建；10 个服务均 Up，api/postgres/mailpit 为 healthy |

## 边界

- 每条退货只有一个粗粒度分类，不支持多标签、二级原因或修改分类；原因自由文本仍保留。
- 聊天归类是关键词规则，不调用模型；可能误判，人工可在后台查看原文但不能改分类。
- 分析按申请创建时间滚动窗口，不是自然日/自然月；已撤回申请计入总数和原因分布。
- 退款总额只累计退货申请上已记录的退款金额，不等同财务总退款；没有按金额加权的退货率。
- Top SKU 最多 5 个，按退货件数排序；没有销售额/订单量分母，因此不是退货率。
- 没有导出 CSV、同比/环比、按坐席或类目维度、原因趋势图；数据量按窗口全量在内存聚合。
- 历史数据没有真实分类，统一落在「其他」，不能反推。
