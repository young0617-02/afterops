# 客服话术模板

- 验证时间：2026-11-05
- 范围：店主维护共享话术，客服在工单会话中一键套用
- 依赖：新表 `quick_replies`（迁移 `f7a9c1e3d5b8`）

## 行为

1. 店主在后台「话术」页创建模板：标题（≤120 字，商家内唯一）、分类（可选，≤80 字）、内容（≤1000 字）。
2. 模板默认启用；店主可停用/重新启用，停用后客服的选择列表不再出现，但历史消息不受影响。
3. `GET /api/admin/quick-replies` 默认只返回启用模板；店主可传 `include_disabled=true` 查看全部，客服传该参数仍只返回启用模板。
4. 创建与修改仅店主可用，客服访问写接口返回 403；列表读取店主与客服均可用。
5. 客服在「人工工单 → 查看会话」展开回复区后，从「套用话术…」选择模板，内容填入回复输入框，可继续编辑后再发送；不会自动发送。
6. 创建/修改写 `quick_reply_created` / `quick_reply_updated` 审计，记录模板 ID、标题、分类、状态与操作人；修改仅在字段实际变化时写审计。
7. 没有删除接口；重复标题返回 409，空标题/内容或超长由接口返回 422。

## 验证结果

| 项 | 结果 |
|---|---|
| 后端测试 | `tests/test_quick_replies.py`：店主创建/列表/改内容/停用、停用后默认列表排除、`include_disabled` 可见、重复标题 409、空标题 422；客服只读启用模板、`include_disabled` 对客服无效、创建/修改 403；审计事件顺序正确 |
| 后端全量 | `uv run pytest -q`：**229 passed**；`ruff check app tests` 通过 |
| 前端检查 | `npm run typecheck`、Docker `npm run build` 通过 |
| 前端 E2E | `npm run test:e2e`：**30 passed**；店主创建话术后，客服在工单中套用并发送，后台线程显示模板内容 |
| 迁移 | 真实 PostgreSQL `upgrade head` → `downgrade -1` → `upgrade head` 通过 |
| 任务评测 | `uv run python -m app.scripts.task_eval`：19/19 通过 |
| 审计链 | `uv run python -m app.scripts.verify_audit`：merchant=5 OK |
| 容器 | api/web 已重建；Compose 10 个服务均 Up，api/postgres/mailpit 为 healthy |

## 边界

- 模板是商家级共享，没有个人话术、团队分组或按队列隔离。
- 不支持变量占位符（订单号/顾客名）、自动填充、富文本或图片；套用后仍需人工编辑。
- 没有使用次数、最近使用、效果统计或排序；列表按分类 + 标题固定排序。
- 没有草稿/审批流；模板修改即时生效，但不会改写已发送消息。
- 禁用只影响后续选择；已填入输入框的内容仍可发送。
- 无删除、批量导入导出或权限细分；店主可管理全部模板。
