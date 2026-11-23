# 售后质量告警

- 验证时间：2026-11-05
- 范围：低分满意度告警、已接管工单解决超时提醒、绩效解决 SLA 列
- 依赖：满意度 `satisfaction_ratings`、工单 `handoff_tickets`、通知/审计

## 行为

1. 顾客提交 1–2 星评价时：
   - 写审计事件 `low_csat_alert`（工单、分数、评论、解决人）；
   - 向所有启用员工发送站内通知/客服邮件，标题「低分评价 TK-…」。
2. 已接管工单超过 `HANDOFF_RESOLUTION_SLA_HOURS`（默认 24 小时）仍未解决：
   - worker 定时发送一次「工单处理超时」提醒；
   - 写审计事件 `handoff_resolution_sla_reminder`，按工单去重。
3. 坐席绩效新增「解决 SLA 超时」列：窗口内解决的工单中，创建到解决超过 24 小时的数量。
4. 后台最近评价对 1–2 星使用红色徽标，便于快速发现。
5. 工单搜索修正加载竞态：搜索请求的结果不会再被更早的未过滤响应覆盖。

## 验证结果

| 项 | 结果 |
|---|---|
| 后端测试 | `tests/test_satisfaction_api.py` 低分评价写审计并通知 2 名员工；`tests/test_sla.py` 已接管 25 小时工单提醒一次、去重；`tests/test_agent_performance_api.py` 长解决工单计入解决 SLA 超时 |
| 后端全量 | `uv run pytest -q`：**213 passed**；`ruff check app tests` 通过 |
| 前端检查 | `npm run typecheck`、Docker `npm run build` 通过 |
| 前端 E2E | `npm run test:e2e`：**27 passed**；工单搜索在大量积压工单下稳定定位 |
| 任务评测 | `uv run python -m app.scripts.task_eval`：19/19 通过 |
| 审计链 | `uv run python -m app.scripts.verify_audit`：merchant=5 OK |
| 实机数据 | 当前 dev 数据未产生 1–2 星评价或超 24 小时已接管工单，因此通知表暂无两类记录；告警逻辑由上述测试覆盖 |

## 边界

- 低分阈值固定为 ≤2 星，不可配置；只通知员工，不会自动回访顾客或创建工单。
- 解决 SLA 对所有优先级统一 24 小时，不按优先级分层；只提醒一次，不做升级或自动改派。
- 绩效按解决时间窗口统计，不包含当前已超时但尚未解决的工单。
- 员工通知走站内/客服邮件，未接短信或 IM；没有告警确认/关闭状态。
- 低分评价没有商家回复流程；评价隐藏与低分告警相互独立。
