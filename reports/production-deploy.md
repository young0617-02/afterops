# 生产部署档与 Caddy TLS

- 验证时间：2026-11-04
- 范围：独立生产 Compose、Caddy 反向代理、TLS、强密钥与端口收敛
- 文件：`infra/docker-compose.prod.yml`、`infra/Caddyfile`、`infra/.env.prod.example`、`docs/deployment.md`

## 栈结构

- `postgres`：仅容器网络，无宿主端口；独立 `prod_pgdata` 卷。
- `api`：`APP_ENV=prod`，迁移后启动；不暴露宿主端口。
- `worker`：outbox/自动完成/超时释放/SLA；不暴露宿主端口。
- `web`：Next.js 独立生产构建；不暴露宿主端口。
- `backup`：默认每日 `pg_dump` + uploads/审计锚点归档，SHA-256 与 7 天保留清理；备份卷独立。
- `mailpit`：SMTP 与 UI 仅容器网络；本地验证用。
- `caddy`：唯一对外入口，映射 `${HTTP_PORT:-8080}` / `${HTTPS_PORT:-8443}`；`/api/*`、`/health*` 转 API，其余转 Web，`/metrics` 返回 404。
- `SITE_ADDRESS=afterops.localhost` 时 Caddy 使用本地 CA；真实域名时自动 HTTPS。

## 验证结果

| 项 | 结果 |
|---|---|
| Compose 配置 | `docker compose -f docker-compose.prod.yml --env-file ... config -q` 通过 |
| 隔离启动 | 独立项目 `afterops-prod`，与开发栈并存；仅 Caddy 暴露 8080/8443 |
| HTTPS 健康 | `https://afterops.localhost:8443/health` → 200，响应含 HSTS、CSP、nosniff、DENY |
| Web 入口 | `https://afterops.localhost:8443/` → 200 |
| 指标阻断 | `https://afterops.localhost:8443/metrics` → 404 |
| Secure Cookie | 管理员登录 200；`afterops_admin` 为 `HttpOnly; Secure; SameSite=Lax`，CSRF Cookie 也为 `Secure` |
| 端口收敛 | `postgres/api/web/worker` 均无宿主端口映射；Caddy 仅映射 80/443 容器端口 |
| 数据 | 验证时向隔离卷导入演示种子；验证后 `down -v` 清理，未改开发栈数据 |

## 边界

- 本地验证使用 Caddy 内部 CA，`curl -k` 才能通过；未验证真实域名 DNS/ACME 证书签发。
- 本地映射 8443 时，Caddy 的 HTTP→HTTPS 跳转仍指向 443；生产按 80/443 映射即可，不要把该本地跳转当作最终配置。
- 观测通过可选 overlay `docker-compose.observability.yml` 接入 Prometheus/Alertmanager/Alert sink/Grafana/Tempo；默认只绑回环端口，详见 [production-observability.md](production-observability.md)。
- 邮件仍是 Mailpit，未接生产 SMTP、退信或告警渠道。
- 单机 Compose，不是多机高可用；备份已由 sidecar 每日落本地卷，未接异地/对象存储与 PITR。
- 密钥通过 env 文件注入；生产应使用平台 secret 管理，不把 `.env.prod` 入库。
