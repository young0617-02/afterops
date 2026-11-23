# 生产观测 overlay

- 验证时间：2026-11-04
- 范围：生产 Compose 可选叠加 Prometheus / Alertmanager / Alert sink / Grafana / Tempo
- 文件：`infra/docker-compose.observability.yml`

## 用法

```bash
docker compose -p afterops-prod \
  -f infra/docker-compose.prod.yml \
  -f infra/docker-compose.observability.yml \
  --env-file infra/.env.prod up -d
```

- 观测端口默认只绑定 `127.0.0.1`：Prometheus 19090、Alertmanager 19093、Grafana 13001、Tempo 13200、alert-sink 18081（均可用环境变量覆盖）。
- API 自动设置 `OTEL_EXPORTER_OTLP_ENDPOINT=http://tempo:4318`，并把 `service.name` 设为 `afterops-api`。
- 复用开发栈的 Prometheus 规则、Alertmanager 路由、Grafana datasource/dashboard 与 Tempo 配置。
- `/metrics` 仍不通过 Caddy 对外暴露；Prometheus 走容器网络抓取。

## 验证结果

| 项 | 结果 |
|---|---|
| Compose 配置 | `docker compose -f prod -f observability --env-file ... config -q` 通过 |
| Prometheus target | `afterops-api` health=`up` |
| 告警规则 | `afterops-api` 组加载 4 条规则 |
| Grafana | `/api/health` = 200，版本 13.3.0；datasources = Prometheus + Tempo |
| Tempo | `/ready` = 200；API `/health` 请求产生真实 trace `8bc7fa8e5b0be4cacb5b818a8be35f9d`（GET /health） |
| Alertmanager | `/-/ready` = 200 |
| Alert sink | GET `/alerts` = 200 |
| 隔离性 | 独立 `afterops-prod` 项目与卷；验证后 `down -v`，开发栈未受影响 |

## 边界

- 观测端口只在本机回环，未接公网/VPN 访问控制；生产如需远程访问应经反向代理与认证。
- Grafana 匿名 Viewer 开启，admin 密码来自环境变量；未接 SSO。
- Prometheus 保留 7 天，Tempo 本地磁盘，未接长期存储/对象存储。
- Alertmanager 仍发往本地 alert-sink 与 Mailpit，未接生产 IM/电话/短信。
- 单机 Compose，无 Thanos/多副本/高可用；未验证大规模指标/追踪容量。
