# 可观测与告警（本地 dev）

- 验证时间：2026-11-02
- API：`http://127.0.0.1:8000`
- Prometheus：`http://127.0.0.1:9090`（仅回环，镜像 `prom/prometheus:v3.15.0`）
- Alertmanager：`http://127.0.0.1:9093`（仅回环，镜像 `prom/alertmanager:v0.34.2`）
- Alert sink：`http://127.0.0.1:18080`（仅回环，本地 webhook 接收器）
- Mailpit：`http://127.0.0.1:8025`（仅回环，镜像 `axllent/mailpit:v1.33.0`，本地 SMTP 收件箱）
- Grafana：`http://127.0.0.1:3001`（仅回环，镜像 `grafana/grafana:13.3.0`，本地 admin/admin，匿名 Viewer）
- Tempo：`http://127.0.0.1:3200`（仅回环；OTLP HTTP `127.0.0.1:4318`；运行时 v3.0.0，数据卷 `tempo_data`）

## 已验证

| 项 | 结果 |
|---|---|
| 请求 ID | `X-Request-Id` 可透传/生成，响应头回显 |
| API 指标 | `GET /metrics` 输出请求计数 + histogram（bucket/sum/count） |
| 结构化日志 | 容器 `LOG_FORMAT=json`，请求日志含 request_id/method/path/status/duration_ms/trace_id |
| 追踪导出 | API 通过 OTLP HTTP 向 Tempo 上报；`GET /api/search` 可检索到 `afterops-api` 的 trace |
| 追踪详情 | 按 trace ID 查询成功（示例 `2682cabb1c0488467ce983a3bebc11f7`，`GET /health`，4 个 span） |
| Grafana → Tempo | `Tempo` 数据源指向 `http://tempo:3200`；经 Grafana 代理按 trace ID 查询 HTTP 200 |
| 日志关联 | API JSON 日志携带 `trace_id`，与 Tempo 中的 trace 对应 |
| Prometheus 抓取 | target `afterops-api` = `up` |
| Prometheus → Alertmanager | `activeAlertmanagers` 包含 `http://alertmanager:9093/api/v2/alerts` |
| 告警规则 | `afterops-api` 组，4 条：API down / 5xx 比例 / p95 / 429 |
| 告警投递 | 合成告警 `AfterOpsSyntheticTest` 到达 sink |
| 邮件告警 | 合成告警 `AfterOpsEmailTest` / `AfterOpsEmailPinnedTest` 到达 Mailpit 收件箱 |
| 断流演练 | 停 API 48s → `AfterOpsApiDown` firing 到达 sink → 重启 API → resolved 到达 sink |
| 恢复后业务 | API `/health` 200；Playwright E2E 5 passed（购买/取消/驳回/agent 只读/移动端） |
| Grafana 数据源 | `Prometheus` → `http://prometheus:9090` |
| Grafana 看板 | `AfterOps · API`，`/d/afterops`，HTTP 200 |
| 日志轮转 | Docker json-file `max-size=10m`、`max-file=3` |

## 限定

- Alertmanager 路由到本地 `alert-sink` + Mailpit SMTP；未接生产邮件/IM/电话，sink 与 Mailpit 都是验证用接收器，不落长期存储。
- 追踪为单服务本地 Tempo，未接生产采样策略、尾部采样或长期 Trace 存储；Tempo 镜像用 `grafana/tempo:latest`（运行时 v3.0.0），当前镜像源无法拉取 `v3.0.0` 固定 tag。
- 未做长期指标存储、日志集中查询。
- Prometheus 保留 7 天（本地 volume）；单 API 进程；数据来自 dev。
- 本报告不构成生产可用性、容量或 SLA 结论。
