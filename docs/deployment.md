# 生产部署手册

适用：单台 Linux VPS / 云主机，Docker Engine + Compose v2。

## 1. 准备环境

```bash
cp infra/.env.prod.example infra/.env.prod
# 编辑 infra/.env.prod，替换全部密码和密钥
```

至少需要：

- `POSTGRES_PASSWORD`：数据库密码
- `SESSION_SECRET`：≥32 字符随机串
- `MOCK_PSP_SECRET`：≥16 字符随机串
- `DEMO_OWNER_PASSWORD` / `DEMO_AGENT_PASSWORD`：初始化管理员密码
- `SITE_ADDRESS`：域名，例如 `shop.example.com`
- `PUBLIC_BASE_URL`：对外地址，例如 `https://shop.example.com`
- `HTTP_PORT` / `HTTPS_PORT`：默认 80 / 443

可用 `openssl rand -hex 32` 生成密钥。

## 2. 启动

```bash
docker compose -p afterops-prod \
  -f infra/docker-compose.prod.yml \
  --env-file infra/.env.prod \
  up -d --build
```

入口：

- `https://${SITE_ADDRESS}/`：店面与后台
- `https://${SITE_ADDRESS}/health`：API 健康检查

Caddy 对真实域名自动申请/续期证书；本地 `afterops.localhost` 使用内部 CA。

## 3. 可观测（可选）

```bash
docker compose -p afterops-prod \
  -f infra/docker-compose.prod.yml \
  -f infra/docker-compose.observability.yml \
  --env-file infra/.env.prod up -d
```

本机回环端口：Prometheus 19090、Alertmanager 19093、Grafana 13001、Tempo 13200、alert-sink 18081。生产远程访问请经 VPN/反向代理并加认证；`/metrics` 不通过 Caddy 暴露。

## 4. 首次数据

生产库默认为空。演示种子只用于本地/验收：

```bash
# 仅演示环境；会重建演示商家
docker compose -p afterops-prod -f infra/docker-compose.prod.yml \
  --env-file infra/.env.prod exec api \
  python -m app.scripts.seed --sqlite-path /tmp/shop.db --reset
```

真实商家数据应通过商品/库存/订单接入流程导入，不要在生产库执行演示 seed。

## 5. 运维

- 迁移：API 容器启动时自动 `alembic upgrade head`。
- 备份：`backup` sidecar 默认每日 `pg_dump` + `uploads` + 审计锚点卷归档，SHA-256，保留 `BACKUP_RETENTION_DAYS`（默认 7）天；部署后可用 `docker compose ... run --rm -e BACKUP_ONCE=1 backup` 立即备份。
- 恢复演练：定期用 `backup_drill.py` 在临时库恢复并校验；备份卷需异地/对象存储另存，避免与主库同盘。
- 发信：将 `SMTP_HOST/PORT/FROM` 指向生产 SMTP；当前 Compose 内置 Mailpit 仅本地验证。
- 观测：生产档未包含 Prometheus/Grafana/Tempo；按需接入外部监控。
- 升级：先备份，再 `up -d --build`；失败时回滚镜像与数据库备份。
- 密钥：不要把 `.env.prod` 提交到版本库；使用主机 secret 或云 secret 管理。

## 6. 已知边界

- 单机部署，不是高可用；数据库与 uploads 卷是单点。
- 本地 8443 映射下 HTTP 跳转仍指向 443，这是 Caddy 端口认知差异；生产使用 80/443。
- 未接 WAF、CDN、对象存储、生产短信/支付/物流。
