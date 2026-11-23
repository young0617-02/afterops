# 审计链查询与完整性校验

> 后续更新：审计链头已接入独立卷 HMAC 签名锚点，见 [audit-anchors.md](audit-anchors.md)。本页数字为当时快照。

- 验证时间：2026-11-03
- 范围：审计事件搜索/筛选/分页、整链哈希校验、后台审计页、JSON 导出
- 入口：`/admin` → 审计链；`agent` 可读，不提供修改入口

## 接口

- `GET /api/admin/audit?event_type=&q=&proposal_id=&conversation_id=&before_id=&limit=50`：按商家返回审计事件（倒序），附事件类型列表与筛选后总数；`q` 搜索载荷与事件类型。
- `GET /api/admin/audit/verify`：按 `prev_hash`/`row_hash` 校验整条商家链，返回 `valid`、已校验条数、首个断裂事件 ID。
- `GET /api/admin/audit/export?event_type=&q=&proposal_id=&conversation_id=&limit=`：下载 JSON（含过滤条件、链校验状态、事件载荷与哈希）；默认最多 1000 条，超出标记 `truncated`。
- 未登录返回 401；`owner` 与 `agent` 均可读。

## 页面

- 顶部显示「哈希链完整 / 哈希链断裂」、链上校验条数、当前筛选条数。
- 事件卡展示类型、时间、订单/意图/动作/金额摘要；展开可看完整 JSON 载荷、`prev` 与 `row` 哈希。
- 支持按订单号/运单号/提案号等关键词搜索，支持下载 JSON；导出按钮由浏览器下载，文件名为 `afterops-audit-*.json`。
- 支持按事件类型筛选、关键词搜索、分页加载更多、重新校验与导出。
- 浏览器实测（msedge 1440×1000）：整链校验通过，载荷与哈希可展开查看。

## 验证结果

| 项 | 结果 |
|---|---|
| 审计 API 测试 | `tests/test_audit_api.py`：列表/筛选/分页/权限、`agent` 可读、整链校验、关键词搜索、空结果、JSON 导出与文件头 |
| 篡改检测 | 直接改写一条事件载荷后，`/audit/verify` 返回 `valid=false`、`broken_at` 指向该事件、`checked_events` 截止到断裂点 |
| 后端全量 | `uv run pytest -q`：**106 passed**；`ruff` 通过 |
| 前端 E2E | `npm run test:e2e`：**8 passed**；首条用例在退款审批后打开审计页，搜索无结果、按订单号命中、展开载荷并完成 JSON 下载 |
| 任务评测 | `uv run python -m app.scripts.task_eval`：12/12 通过 |

## 边界

- 这里的“完整”是**链内哈希自洽**：能发现单条/局部改写，但拥有数据库写权限的人可以重算整条链。未接 WORM、第三方时间戳或外部存证。
- 校验每次读取整条商家链，当前为单机 dev 数据量；未做增量校验或生产规模压测。
- 导出为本地 JSON 下载，不含外部签名/第三方时间戳；页面与导出载荷对店主/客服可见，聊天回复文本会进入载荷。
- 修复断裂事件后不会自动重算；当前也没有修复入口。
