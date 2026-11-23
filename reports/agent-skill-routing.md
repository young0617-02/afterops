# 坐席技能路由

- 验证时间：2026-11-09
- 范围：为客服配置售后/投诉工单技能，自动分配只选择在线、接单中且技能匹配的客服；未配置技能视为全部队列
- 迁移：`a0b2c4e6f8d1`（`users.handoff_skills`）

## 行为

1. 客服账号可配置工单技能集合，目前取值为 `after_sales`（售后）与 `complaint`（投诉）；空集合表示接收全部队列。
2. `PATCH /api/admin/staff/{id}/skills` 仅店主可调用；只能配置客服账号，未知队列 422，更新写 `staff_skills_updated` 审计（技能集合、操作人）。
3. 自动分配在原有「启用 + 接单中 + 心跳在线」条件上增加队列匹配：客服技能为空则全部匹配，否则工单队列必须在技能集合内。
4. 匹配客服仍按当前处理中工单数最少、用户 ID 排序；没有任何匹配客服时工单保持 `open` 待接管，不会降级给不匹配或离线账号。
5. 手动指派/改派不受技能限制，店主可覆盖自动路由。
6. 团队页每个客服卡展示技能徽章与「售後/投诉」勾选框；勾选即时保存并刷新，全部取消后显示「全部队列」。
7. 自动分配设置说明同步为「心跳在线、接单中、技能匹配、负载最少」。

## 验证结果

| 项 | 结果 |
|---|---|
| 后端测试 | `tests/test_handoff_auto_assign.py`：技能更新与列表字段；客服无权修改 403；未知技能 422；店主账号不能配置 422；售后工单只分给全队列客服；投诉工单分给技能匹配客服；全部客服仅投诉技能时售后工单保持 open |
| 后端全量 | `.venv/Scripts/python.exe -m pytest -q`：**300 passed**；`ruff check app tests` 通过 |
| 前端检查 | `npm run typecheck`、Docker `npm run build` 通过 |
| 前端 E2E | `npx playwright test`：**39 passed**；团队页用例增加「新建客服勾选投诉技能 → 徽章显示」 |
| 迁移 | 真实 PostgreSQL `upgrade head` → `downgrade -1` → `upgrade head` 通过 |
| 任务评测 | `python -m app.scripts.task_eval`：19/19 通过 |
| 审计链 | `python -m app.scripts.verify_audit`：merchant=5 OK |
| 容器 | api/web/worker 已重建；10 个服务 Up，api/postgres/mailpit 为 healthy |

## 边界

- 只有两个队列（售后、投诉），不支持按意图、商品类目、语言、等级或商家自定义队列。
- 没有技能熟练度、权重、优先级或回退队列；无匹配时只保持待接管。
- 技能是静态集合，不包含排班、在线时长、最大并发或技能过期。
- 手动指派不校验技能，可能指派给不匹配客服；不做强制拦截。
- 技能变更即时生效于新工单，不影响已分配工单。
