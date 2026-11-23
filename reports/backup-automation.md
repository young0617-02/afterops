# 生产备份 sidecar 与保留策略

- 验证时间：2026-11-04
- 范围：生产 Compose 定时备份 PostgreSQL + uploads，SHA-256 校验与保留清理
- 文件：`infra/backup.sh`、`infra/docker-compose.prod.yml`

## 行为

- `backup` 服务基于 `postgres:16-alpine`，默认每 24 小时执行一次。
- 生成 `afterops-<UTC时间>.sql.gz`（`pg_dump | gzip`）、`afterops-uploads-<UTC时间>.tar.gz`（uploads 卷）与 `afterops-anchors-<UTC时间>.tar.gz`（审计锚点卷）。
- 每个归档生成 `.sha256`；使用临时文件写入后原子改名，避免半成品。
- 按 `BACKUP_RETENTION_DAYS`（默认 7 天）删除过期数据库备份、uploads/anchors 归档与校验文件。
- 支持 `BACKUP_ONCE=1` 单次执行，便于部署后立即备份或运维演练。

## 验证结果

| 项 | 结果 |
|---|---|
| Compose 配置 | `docker compose -f docker-compose.prod.yml --env-file ... config -q` 通过 |
| 单次备份 | 隔离生产栈执行 `BACKUP_ONCE=1`：`db=15149B uploads=143B retention=7d` |
| 校验和 | `sha256sum -c *.sha256` 两个归档均 OK |
| gzip 完整性 | `gzip -t` 通过 |
| uploads 归档 | `tar -tzf` 含 `backup-test.txt`，内容回读为 `backup-test` |
| 审计锚点归档 | 同次备份 `anchors=447B`；`tar -tzf` 含 `audit-anchors-5.jsonl` |
| 保留清理 | 预置 2026-10-20 的旧备份，下一次备份后旧文件与旧校验文件均被删除，新备份保留 |
| 隔离性 | 使用独立项目 `afterops-prod` 与独立卷；验证后 `down -v` 清理，未影响开发栈 |

## 边界

- 备份存放在同一 Docker 卷，未接对象存储/异地/跨机复制；主机或卷损坏会同时丢主库与备份。
- 未接 PITR/WAL 归档；恢复粒度是最近一次逻辑备份。
- 备份失败只写容器日志，未接告警；生产应接入外部监控。
- 默认 24 小时一次，RPO 最坏接近 24 小时；retention 以文件 mtime 计算。
- uploads/anchors 归档只备份，不会自动恢复到线上卷；恢复演练仍用 `backup_drill.py`。
- 数据库导出为明文 SQL gzip，备份卷需按敏感数据保护。
