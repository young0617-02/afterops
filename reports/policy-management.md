# 政策版本管理

- 验证时间：2026-11-04
- 范围：后台政策库、发布新版本、版本闭合、审计与 AI 引用
- 相关：`backend/app/services/policies.py`、`backend/app/api/admin_policies.py`、`frontend/components/AdminPolicies.tsx`

## 行为

1. 后台「政策」页按主题展示政策条目：现行版本、有效期、来源、正文与完整版本历史。
2. 店主可新增政策条目，或在既有条目下发布新版本；客服只读。
3. 版本号自动递增（`v1`、`v2`…），已发布版本不可编辑/删除。
4. 发布新版本时，上一版本 `valid_until` 自动闭合到新版本生效日；新版本生效日必须晚于最新版本。
5. 同一生效日不允许重复发布；同日修订请选择更晚的生效日（当前 schema 要求 `valid_until > valid_from`）。
6. 一个主题只允许一个政策条目；新增重复主题会被拒绝。
7. 发布写审计事件 `policy_published`，包含条目、版本、生效日、上一版本与操作人。
8. 顾客政策接口与 AI 检索继续按日期读取现行版本；AI 回复中的引用形如 `POLICY-RETURN@v3`，并写入 `chat_answered` 审计。

## API

| 方法 | 路径 | 权限 | 说明 |
|---|---|---|---|
| GET | `/api/admin/policies` | 店主/客服 | 按条目分组，含现行版本与全部历史 |
| POST | `/api/admin/policies` | 仅店主 | 发布新条目或新版本 |

请求字段：`entry_id`、`topic`、`title`、`body`、`source`、`valid_from`（默认今天）。`entry_id` 需为 3–64 位大写字母/数字/连字符，`topic` 需为 2–64 位小写字母/数字/下划线。

## 验证结果

| 项 | 结果 |
|---|---|
| 后端测试 | `tests/test_admin_policies_api.py`：分组与权限、发布闭合上一版本、历史日期仍命中旧版本、同日重复拒绝、新条目/重复主题/改主题/非法格式、审计事件、AI 引用新版本 |
| 后端全量 | `uv run pytest -q`：**176 passed**；`ruff check app tests` 通过 |
| 前端检查 | `npm run typecheck`、Docker `npm run build` 通过 |
| 前端 E2E | `npm run test:e2e`：**22 passed**；店主新增政策 → 发布 v2 → 看到版本历史（2） |
| 任务评测 | `uv run python -m app.scripts.task_eval`：19/19 通过；政策任务仍为现行 v2 / 2026-03-01 命中 v1 |
| 审计链 | `uv run python -m app.scripts.verify_audit`：merchant=5 OK |

## 边界

- 不提供编辑、删除、撤回已发布版本，也没有草稿/审批流；发布即生效（可指定未来生效日）。
- 同日修订被拒绝：`valid_until > valid_from` 的约束不允许零天版本；需要改规则时选择次日或更晚生效。
- 一个主题只允许一个条目，不支持同一主题下并行多套政策（如按会员等级/地区）。
- 没有政策变更通知、邮件或顾客确认；AI 在下次回答时使用新版本。
- 政策正文为纯文本，没有结构化条款、变量、附件或富文本编辑器。
- 版本历史只记录元数据，不提供版本间 diff；正文需逐版本查看。
- 政策只被 AI 引用，不直接驱动资金审批阈值；退款/补偿仍需人工审批。
