# 安全基线与生产配置守卫

- 验证时间：2026-11-03
- 范围：API/Web 安全响应头、跨站写请求来源校验、CSRF 双重提交令牌、生产默认密钥启动拦截

## 已落地

- API 所有响应：
  - `X-Content-Type-Options: nosniff`
  - `X-Frame-Options: DENY`
  - `Referrer-Policy: no-referrer`
  - `Permissions-Policy: camera=(), microphone=(), geolocation=()`
  - `Cross-Origin-Opener-Policy: same-origin`
  - `Content-Security-Policy: frame-ancestors 'none'; base-uri 'none'`
  - `APP_ENV=prod` 时附加 HSTS。
- Web 响应：nosniff、DENY frame、Referrer/Permissions Policy、frame-ancestors/base-uri CSP。
- 写请求（POST/PUT/PATCH/DELETE）如果携带 `Origin`，必须命中 `ALLOWED_ORIGINS`；跨站 Origin 返回 403 `origin_not_allowed`。无 Origin 的服务端/CLI 请求保持可用。
- CSRF 双重提交 + 会话绑定：
  - 顾客登录下发 `afterops_csrf`，管理员登录下发 `afterops_admin_csrf`；值为 `HMAC-SHA256(SESSION_SECRET, "csrf:" + 会话 token)`，随会话轮换，Cookie 非 HttpOnly、SameSite=Lax，生产加 Secure。
  - 携带会话 Cookie 的写请求必须带匹配的 `X-CSRF-Token` / `X-Admin-CSRF-Token`，缺失或不匹配返回 403 `csrf_token_invalid`；顾客与管理员会话同时存在时各自独立校验。
  - 安全 GET 会自动补发缺失或与当前会话不匹配的 CSRF Cookie，兼容部署前已建立的会话；登出同时删除会话与 CSRF Cookie。
  - `/api/webhooks/*` 由签名校验保护，登录相关 POST 不要求 CSRF；Origin 白名单校验仍先于 CSRF 执行。
- 商品主图：仅店主可上传/移除；按文件头识别 PNG/JPEG/WebP，限制 5MB，UUID 文件名并做路径穿越校验；店面只公开在售商品主图，草稿/下架主图仅登录后台可读。
- 生产入口：独立 Compose 只暴露 Caddy 的 80/443；API/数据库/Web/worker 无宿主端口；Caddy 终止 TLS，`/metrics` 返回 404，生产 Cookie 为 Secure；本地内部 CA 验证报告 [production-deploy.md](production-deploy.md)。
- `APP_ENV=prod` 启动校验：拒绝默认 `SESSION_SECRET`、默认 `MOCK_PSP_SECRET`、默认演示店主/客服密码；要求会话密钥至少 32 字符、支付密钥至少 16 字符。
- 原有防线保留：HttpOnly + SameSite=Lax Cookie、OTP 限流、登录/聊天/结算限流、Argon2 密码、归属校验、审计链。

## 验证结果

| 项 | 结果 |
|---|---|
| 安全测试 | `tests/test_security.py`：安全头、恶意 Origin 403、允许 Origin 202、GET 忽略 Origin、生产默认密钥拒绝启动、强密钥通过 |
| CSRF 测试 | `tests/test_csrf.py`：登录下发绑定会话的 CSRF Cookie（HttpOnly 标记正确）、顾客写请求缺失令牌 403、伪造令牌 403、安全 GET 自动补发、管理员独立校验、webhook 豁免 |
| 实机探测 | 登录后无令牌加购 **403**；带 `afterops_csrf` 值加购 **200** |
| 后端全量 | `uv run pytest -q`：**167 passed**；`ruff` 通过 |
| 任务评测 | `uv run python -m app.scripts.task_eval`：19/19 通过（脚本已自动携带 CSRF 头） |
| 前端 E2E | `npm run test:e2e`：18 passed（浏览器同源写请求未被误拦） |

## 边界

- CSRF 令牌按会话派生、随登录轮换，但不按请求一次性轮换，也不绑定设备指纹；同一浏览器内仍由 SameSite=Lax + Origin 白名单共同防护。
- CSRF Cookie 需被同源 JS 读取，因此无法抵御同源 XSS；当前 CSP 未做脚本 nonce/hash。
- Origin 白名单仍是独立防线；无 Origin 的手工/服务端请求不因 Origin 被拦，但携带会话 Cookie 时仍须通过 CSRF 校验。
- 没有 WAF、速率限制之外的 DDoS 防护、依赖漏洞扫描或第三方渗透测试。
- CSP 只限制 frame-ancestors/base-uri，未做严格的脚本 nonce/hash CSP。
- HSTS 仅在应用判断 `APP_ENV=prod` 时返回；TLS 仍应由反向代理终止。
- `ALLOWED_ORIGINS` 默认含本地四个地址；生产必须显式替换。
- 本地 Docker Compose 为 18 条 E2E 将 `ADMIN_LOGIN_LIMIT` 放宽到 50；代码默认仍为 5，生产应使用严格值。
