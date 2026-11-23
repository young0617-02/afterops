# 坐席绩效

- 验证时间：2026-11-05
- 范围：按坐席统计工单待处理、接管、解决、平均耗时、首次响应与 SLA 超时
- 页面：后台「团队」页新增坐席绩效表

## 行为

1. `GET /api/admin/agent-performance?hours=` 返回窗口内每个商家账号的绩效；窗口支持 1–720 小时，默认 168 小时。
2. 指标：
   - 待处理：当前 `claimed_by` 为该账号且状态为 `open/claimed` 的工单数；
   - 窗口内接管：`claimed_at` 落在窗口内的工单数；
   - 窗口内解决：`resolved_at` 落在窗口内的工单数；
   - 平均接管：接管时间 − 工单创建时间；
   - 平均解决：解决时间 − 工单创建时间；
   - 首次响应数 / 平均首响：窗口内有客服回复的工单，按回复审计操作人归因；
   - 首响 SLA 超时：窗口内接管的工单中，无首响或首响超过 30 分钟阈值的数量；
   - SLA 超时：窗口内接管的工单中，接管耗时超过优先级阈值（紧急 5 / 高 10 / 普通 15 分钟）的数量。
3. 顶层返回未分配待接管工单数（`status=open`），不属于任何坐席。
4. 团队页提供近 24 小时 / 7 天 / 30 天切换与刷新；停用账号保留在表中并标记。
5. 权限：仅店主可查看；客服访问返回 403。
6. 指标全部来自现有 `handoff_tickets`，不新增表与迁移。

## API

`GET /api/admin/agent-performance?hours=168`（仅店主）：

```json
{
  "hours": 168,
  "since": "2026-10-28T...",
  "unassigned_open": 1,
  "agents": [
    {
      "user_id": 2,
      "email": "agent@afterops.local",
      "role": "agent",
      "status": "active",
      "open_tickets": 1,
      "claimed": 2,
      "resolved": 1,
      "avg_claim_minutes": 12.5,
      "avg_resolution_minutes": 25.0,
      "first_responses": 2,
      "avg_first_response_minutes": 25.0,
      "first_response_sla_breaches": 1,
      "sla_breaches": 1
    }
  ]
}
```

## 验证结果

| 项 | 结果 |
|---|---|
| 后端测试 | `tests/test_agent_performance_api.py`：接管/解决/待处理计数、平均耗时、首次响应数与平均耗时/超时、紧急工单 SLA 超时、未分配数、窗口参数校验、客服 403；`tests/test_sla.py`：首响提醒一次与已有回复抑制 |
| 后端全量 | `uv run pytest -q`：**227 passed**；`ruff check app tests` 通过 |
| 前端检查 | `npm run typecheck`、Docker `npm run build` 通过 |
| 前端 E2E | `npm run test:e2e`：**29 passed**；团队页看到未分配待接管、绩效行、平均首响与首响 SLA 列，切换近 24 小时后仍显示窗口起点 |
| 任务评测 | `uv run python -m app.scripts.task_eval`：19/19 通过 |
| 审计链 | `uv run python -m app.scripts.verify_audit`：merchant=5 OK |

## 边界

- 统计接管、解决、首次响应 SLA 与满意度；解决 SLA 提醒见 [service-quality-alerts.md](service-quality-alerts.md)，首响提醒见 [first-response-sla.md](first-response-sla.md)。
- 接管/解决按事件发生时间计入窗口；窗口是滚动小时，不是自然日/自然月。
- 待处理按工单当前 `claimed_by` 归属；改派后历史接管仍记在原坐席名下，但当前待处理归新坐席。
- 未分配待接管是全局数字，不按队列/优先级拆分。
- 停用账号仍出现在表中；没有离职归档或历史快照。
- 没有坐席在线状态、自动分配、工单重开、CSAT 或质检评分。
- 不导出 CSV，不做团队排行或目标达成率；数据量按商家全量工单在内存计算，超大工单量需改为 SQL 聚合。
