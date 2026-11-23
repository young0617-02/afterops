# 审计链外部签名锚点

- 验证时间：2026-11-04
- 范围：worker 定期把审计链头写入独立卷，使用环境密钥 HMAC 签名；后台校验锚点链、行匹配与链头覆盖
- 文件：`backend/app/services/audit_anchors.py`、`backend/app/api/admin_audit_anchors.py`

## 行为

1. worker 首次启动立即执行，之后每 `AUDIT_ANCHOR_INTERVAL_SECONDS`（默认 86400 秒）执行一次。
2. 对每个商家读取最新 `audit_events.id/row_hash`，若与最后锚点相同则跳过。
3. 新锚点字段：商家、audit_id、row_hash、previous signature、anchored_at；使用 `AUDIT_ANCHOR_SECRET` HMAC-SHA256 签名，追加写入 `/data/anchors/audit-anchors-{merchant_id}.jsonl`，flush + fsync。
4. 锚点文件在独立 Docker 卷；密钥在环境变量，不在数据库。
5. 后台审计页显示锚点数量、已锚定/当前链头、链头覆盖状态与最近 5 个锚点。
6. 生产备份 sidecar 同时归档 anchors 卷，并生成 SHA-256。

## 验证结果

| 项 | 结果 |
|---|---|
| 后端测试 | `tests/test_audit_anchors_api.py`：锚点写入、连续锚点 prev 校验、链头覆盖、篡改审计行检测、篡改签名检测、未配置返回 |
| 后端全量 | `uv run pytest -q`：**166 passed**；`ruff` 通过 |
| 前端 E2E | `npm run test:e2e`：**19 passed**；审计页显示锚点区域 |
| 任务评测 | `uv run python -m app.scripts.task_eval`：19/19 通过 |
| 实机 worker | 启动日志 `audit anchors written: 1`；API 返回 `count=2, chain_valid=true, anchored_through_id=7085, current_head_id=7085, head_covered=true` |
| 首次运行修复 | 原 `last_anchor_at=0` 在主机 uptime <24h 时不会首轮执行；改为 `-interval`，启动即锚定 |

## 边界

- 锚点文件与数据库仍在同一主机/同一 Docker 环境，不是第三方时间戳或 WORM 存储；拥有主机 root 权限者可删除/替换锚点文件。
- HMAC 密钥保护“没有密钥不能重算整条锚点链”，但不是非对称签名，无法向第三方公开证明。
- 单 worker 写锚点；多 worker 未做文件锁/幂等竞争测试。
- 锚点只覆盖链头，不导出完整审计事件；完整恢复仍依赖数据库备份/审计 JSON 导出。
- 锚点文件无自动轮转/压缩，长期增长；备份保留策略只作用于备份卷。
