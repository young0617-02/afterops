# AfterOps · AI 售后运营台

## 项目简介

面向单店 3C 配件的售后运营台：完整交易底座（商品 / 库存 / 支付 / 履约 / 售后）+ 事实型 AI 客服 + 店主审批与人工工单 + 哈希链审计。

- AI 只查事实、只提案：退款、补偿、改地址等写操作一律生成待审提案，由店主在后台批准后执行。
- 资金与库存数字全部由数据库/SQL 产出；AI 回复中出现的金额、状态必须可溯源。
- 每个业务事件写入按商家隔离的哈希链审计，worker 定期写 HMAC 签名锚点，可在线校验。

当前边界：支付为 mock PSP；顾客登录 OTP 在非生产环境返回 `debug_code`；物流节点由商家手工推进；客服与顾客邮件走本地 Mailpit；默认 AI 为确定性模板，可切换到 Ollama 或 OpenAI 兼容端点。未接入真实短信、支付、承运商 API。

> 仓库同时保留了 2026-10 的旧原型（根目录 `src/`、`tests/`、`evals/`、`ops/`），见 [旧原型（legacy）](#旧原型legacy)。

## 目录

- [核心功能](#核心功能)
- [技术栈与架构](#技术栈与架构)
- [目录结构](#目录结构)
- [安装与运行](#安装与运行)
- [主要配置](#主要配置)
- [使用示例](#使用示例)
- [测试方式](#测试方式)
- [数据、许可与安全](#数据许可与安全)
- [旧原型（legacy）](#旧原型legacy)
- [贡献说明](#贡献说明)
- [文档索引](#文档索引)
- [已知边界](#已知边界)

## 核心功能

### 店面与交易

- 商品目录、分类、搜索、详情页（主图 + 详情图、评分、评论、保修期展示）。
- 购物车、优惠券（固定金额 / 百分比、全场 / 类目 / SKU 范围）、配送方式与运费规则、结算。
- 模拟支付（mock PSP）与支付回调；订单查询、整单取消、发票申请、地址簿。
- 已签收订单的商品评价（文字 + 最多 3 张图片），商家可回复、隐藏 / 恢复。

### 售后

- 退货：按件部分退货、退货资格校验（类目窗口 / 禁退类目）、回寄物流时间线、逐件质检处置（回补库存 / 报损）。
- 换货：同款或跨 SKU 换货，按件汇总差价生成退款提案。
- 维修：按商品保修期申请，店主报价、批准、收货、完成。
- 退款与补偿：顾客申请生成提案；店主也可为电话 / 线下顾客发起退款、补偿、改地址提案，全部走审批后执行。
- 顾客可分别关闭发货 / 售后 / 退货三类邮件通知。

### AI 客服

- 只读工具查询订单、物流、售后、退款、维修、发票、商品与政策，回答基于数据库事实。
- 高风险输入（退款、赔偿、投诉、价保等）只解释规则并转人工，不作承诺；输出侧再做承诺短语复核。
- 写操作只生成待审提案，AI 不直接执行退款或改地址。
- 顾客聊天支持 SSE 实时消息、最多 3 张图片附件；人工接管后 AI 暂停自动答复。
- Provider 可切换：`template`（默认、确定性）/ `ollama` / `openai`，模型只允许改写已批准的回复。

### 后台

- 概览、商品与图片、库存（导入 / 流水 / 对账 / 盘点 / 仓库调拨）、订单与履约、财务对账、客户目录。
- 提案审批、人工工单（接管 / 指派 / 解决 / 内部备注 / 顾客评价）、快捷回复、政策库。
- 团队与坐席绩效、自动分配、SLA 提醒与升级、邮件 outbox 重试。
- 评价管理、退货分析、维修与发票管理。
- 审计链查看 / 导出 / 校验，HMAC 锚点管理。

### 审计与可观测

- 每个商家一条审计哈希链，可校验、可导出；worker 定期把链头写入独立卷并做 HMAC 签名。
- `/metrics` 暴露请求指标；可选 OpenTelemetry 追踪接入 Tempo。
- 开发栈内置 Prometheus、Alertmanager、Grafana、Tempo、Mailpit 与本地告警接收器。

## 技术栈与架构

| 层 | 技术 |
|---|---|
| 后端 | Python 3.12、FastAPI、SQLAlchemy 2、Alembic、PostgreSQL 16、Pydantic Settings、argon2-cffi、OpenTelemetry |
| 前端 | Next.js 16、React 19、TypeScript；Playwright（E2E） |
| 测试 | pytest、httpx、ruff |
| 基础设施 | Docker Compose；Prometheus、Alertmanager、Grafana、Tempo、Mailpit、alert-sink |

请求路径：

```
浏览器 (Next.js :3000)
  ├─ /api/* 由 next.config.mjs 反代到 API (:8000)
  └─ 后台 /admin、店面 /product/[sku]、购物车、订单、售后、账户、地址、优惠券、帮助中心
        ↓
FastAPI (:8000)
  ├─ /api/store/*  顾客侧接口（OTP 登录、目录、购物车、结算、订单、售后、维修、发票、评价、帮助）
  ├─ /api/admin/*  店主 / 客服接口（提案审批、工单、库存、财务、审计、团队等）
  ├─ /api/chat     会话与 SSE 流
  └─ /health、/health/ready、/metrics、/docs
        ↓
PostgreSQL 16（交易、售后、审计）
        ↑
worker（app.worker.outbox）：邮件出站、SLA 提醒、订单超时释放、审计锚点
```

AI 调用链：用户消息 → 高风险识别 → 只读工具取事实 → 模板或模型改写 → 承诺短语复核 → 提案 / 转人工 → 写审计。

## 目录结构

```
backend/            FastAPI 应用
  app/api/          顾客与后台 HTTP 路由（/api/store、/api/admin、/api/chat）
  app/services/     业务服务（结算、售后、审计、提案、工单、通知等）
  app/domain/       SQLAlchemy 模型与金额工具
  app/ai/           意图、只读工具、Guardrails、Provider、聊天编排
  app/worker/       outbox worker
  app/scripts/      seed、task_eval、verify_audit、backup_drill、压测等
  alembic/          数据库迁移
  tests/            pytest 用例（内存 SQLite fixture，无需外部服务）
frontend/           Next.js 16 店面与后台
  app/              页面路由（店面、购物车、订单、售后、账户、地址、优惠券、帮助、/admin）
  components/       后台面板、聊天窗、通知等组件
  lib/              API 客户端、类型、状态映射
  e2e/              Playwright 用例
infra/              开发 / 生产 Compose，Prometheus、Alertmanager、Grafana、Tempo、Mailpit、alert-sink
docs/               产品文档（product/）、ADR、部署手册与旧原型文档
data/               合成店铺 SQL、意图 / 门禁词表、JDDC 取证（不含原始语料）
reports/            各功能验证记录与评测输出
src/ tests/ evals/ ops/ scripts/   旧原型（legacy），与当前全栈并存
项目地图.md / 交付总结.md / 项目方案-*.md   历史盘点与方案文档
```

## 安装与运行

### 环境要求

以下命令默认在仓库根目录执行。

- Docker Engine / Docker Desktop + Compose v2（全栈与生产部署）。
- 本机开发：Python 3.12 + [uv](https://docs.astral.sh/uv/)、Node 24 + npm（与 `backend/Dockerfile`、`frontend/Dockerfile` 一致）。
- 前端 E2E 使用 Playwright 的 `msedge` 通道，需要本机安装 Microsoft Edge。

### 全栈启动（开发）

```bash
docker compose -f infra/docker-compose.yml up -d --build
```

首次启动后数据库为空，需要导入合成演示数据（脚本从 SQLite 读取并写入 Postgres；默认连接 `127.0.0.1:5433`）：

```bash
(cd backend && uv run python -m app.scripts.seed --reset)
```

主要入口：

| 服务 | 地址 |
|---|---|
| 店面 / 后台 | `http://127.0.0.1:3000`（后台 `/admin`） |
| API / Swagger | `http://127.0.0.1:8000`、`http://127.0.0.1:8000/docs` |
| PostgreSQL | `127.0.0.1:5433`（容器内 5432） |
| Prometheus | `http://127.0.0.1:9090` |
| Alertmanager | `http://127.0.0.1:9093` |
| Grafana | `http://127.0.0.1:3001`（本地 `admin/admin`，匿名 Viewer） |
| Mailpit（邮件收件箱 / SMTP） | `http://127.0.0.1:8025`、`127.0.0.1:1025` |
| 本地告警接收 | `http://127.0.0.1:18080/alerts` |
| Tempo | `127.0.0.1:3200`（查询）、`127.0.0.1:4318`（OTLP） |

查看日志与停止：

```bash
docker compose -f infra/docker-compose.yml logs -f api
docker compose -f infra/docker-compose.yml down
```

### 本机开发（前后端不用容器）

```bash
# 1. 只起 Postgres
docker compose -f infra/docker-compose.yml up -d postgres

# 2. 后端
cd backend
cp .env.example .env
uv sync
uv run alembic upgrade head
uv run python -m app.scripts.seed --reset
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000

# 3. 前端（新终端，在仓库根目录）
cd frontend
npm install
npm run dev          # http://127.0.0.1:3000，/api 反代到 127.0.0.1:8000
```

容器入口会自动执行 `alembic upgrade head`；本机启动需要手动执行迁移。

### 生产部署

```bash
cp infra/.env.prod.example infra/.env.prod   # 替换全部密码与密钥
docker compose -p afterops-prod \
  -f infra/docker-compose.prod.yml \
  --env-file infra/.env.prod up -d --build
```

生产栈使用 Caddy 提供 TLS，包含 `postgres`、`api`、`worker`、`web`、`backup`、`mailpit`、`caddy`；可选观测栈见 [docs/deployment.md](docs/deployment.md)。生产库不执行演示 seed，真实数据应通过商品 / 库存 / 订单接入流程导入。

## 主要配置

后端配置入口为 `backend/.env.example`（复制为 `backend/.env`）；根目录 `.env.example` 属于旧原型，与全栈后端无关。

| 变量 | 默认值 | 说明 |
|---|---|---|
| `APP_ENV` | `dev` | `prod` 时 OTP 不返回 `debug_code`，Cookie 加 `Secure` |
| `DATABASE_URL` | `postgresql+psycopg://afterops:afterops@127.0.0.1:5433/afterops` | 本机默认指向 Compose 暴露的 Postgres |
| `SESSION_SECRET` | `dev-only-change-me` | 会话签名密钥，生产必须替换 |
| `MOCK_PSP_SECRET` | `dev-only-change-me` | 模拟支付回调签名密钥 |
| `DEMO_OWNER_PASSWORD` / `DEMO_AGENT_PASSWORD` | `owner123456` / `agent123456` | seed 创建的店主 / 客服账号密码 |
| `DEMO_MERCHANT_NAME` | `AfterOps 演示 3C 店` | 演示商家名；找不到时接口提示先运行 seed |
| `LLM_PROVIDER` | `template` | `template` / `ollama` / `openai` |
| `OLLAMA_BASE_URL` / `OLLAMA_MODEL` | `http://127.0.0.1:11434` / 空 | 本地模型配置 |
| `OPENAI_BASE_URL` / `OPENAI_API_KEY` / `OPENAI_MODEL` | 空 | 任意 OpenAI 兼容 `/chat/completions` 端点 |
| `LLM_TIMEOUT_MS` | `20000`（容器 `60000`） | 模型调用超时 |
| `EMAIL_NOTIFY_ENABLED` / `CUSTOMER_EMAIL_NOTIFY_ENABLED` | `true` | 员工 / 顾客邮件通知开关 |
| `SMTP_HOST` / `SMTP_PORT` / `SMTP_FROM` | `127.0.0.1` / `1025` / `afterops@local` | 本地默认指向 Mailpit |
| `UPLOAD_DIR` / `UPLOAD_MAX_BYTES` | `uploads` / `5242880` | 附件存储与单文件上限（容器内为 `/data/uploads`） |
| `OTP_TTL_SECONDS` / `OTP_MAX_ATTEMPTS` | `300` / `5` | 登录验证码有效期与尝试次数 |
| `OTP_REQUEST_LIMIT` / `LOGIN_LIMIT` | `5` / `10` | 验证码与登录限流（窗口 600s） |
| `CHAT_LIMIT` / `CHECKOUT_LIMIT` | `30` / `10` | 聊天与结算限流（窗口 60s） |
| `ORDER_AUTO_COMPLETE_DAYS` | `7` | 订单自动完成天数 |
| `PROPOSAL_SLA_MINUTES` / `HANDOFF_SLA_MINUTES` | `30` / `15` | 提案与工单 SLA |
| `HANDOFF_SLA_HIGH_MINUTES` / `HANDOFF_SLA_URGENT_MINUTES` | `10` / `5` | 高优 / 紧急工单 SLA |
| `AUDIT_ANCHOR_DIR` / `AUDIT_ANCHOR_SECRET` | `anchors` / `dev-only-anchor-secret-change-me` | 审计锚点目录与签名密钥 |
| `AUDIT_ANCHOR_INTERVAL_SECONDS` | `86400` | 锚点写入间隔 |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | 空 | 留空关闭追踪；容器内默认 `http://tempo:4318` |
| `LOG_FORMAT` | `text` | `text` / `json` |

前端：

| 变量 | 默认值 | 说明 |
|---|---|---|
| `API_BASE_URL` | `http://127.0.0.1:8000` | `next.config.mjs` 把 `/api/*` 反代到该地址；Docker 构建时传 `http://api:8000` |
| `PLAYWRIGHT_BASE_URL` | `http://127.0.0.1:3000` | E2E 目标地址 |

生产配置见 `infra/.env.prod.example`：`SITE_ADDRESS`、`PUBLIC_BASE_URL`、`POSTGRES_PASSWORD`、`SESSION_SECRET`、`MOCK_PSP_SECRET`、`DEMO_OWNER_PASSWORD`、`DEMO_AGENT_PASSWORD`、`AUDIT_ANCHOR_SECRET`、`BACKUP_*` 等。

## 使用示例

### 店面与后台

1. 打开 `http://127.0.0.1:3000`，浏览商品并加入购物车；结算时可使用优惠券与配送方式。
2. 顾客登录：输入手机号 → 开发环境响应中带 `debug_code`，页面直接显示验证码 → 登录后可查看订单、售后、地址、优惠券与通知设置。
3. 后台 `http://127.0.0.1:3000/admin`：
   - 店主：`owner@afterops.local` / `owner123456`
   - 客服：`agent@afterops.local` / `agent123456`（可查看 / 接管工单，不能审批资金与地址类提案）
4. 在订单或聊天中发起退款 / 退货 / 换货 / 维修请求，到后台「提案」或「售后」列表审批推进。
5. 打开右下角聊天窗提问，例如「订单 ORD-… 什么时候到」「这个 SKU 多少钱」；涉及退款、赔偿等问题时，AI 只解释规则并转人工或生成提案。

### 接口示例

```bash
curl -s http://127.0.0.1:8000/health
curl -s http://127.0.0.1:8000/health/ready
curl -s http://127.0.0.1:8000/metrics | head
curl -s http://127.0.0.1:8000/openapi.json | head -c 300

# 请求登录验证码（开发环境返回 debug_code）
curl -s -X POST http://127.0.0.1:8000/api/store/otp/request \
  -H 'Content-Type: application/json' \
  -d '{"phone":"13800000001"}'
```

带登录态的写请求需要同时携带会话 Cookie 与 CSRF 头，建议通过前端页面操作；接口清单见 `http://127.0.0.1:8000/docs`。

### 运维脚本

```bash
(cd backend && uv run python -m app.scripts.verify_audit)        # 校验所有商家审计链
(cd backend && uv run python -m app.scripts.backup_drill)        # 临时库恢复演练
(cd backend && uv run python -m app.scripts.expire_reservations) # 释放超时库存预留
(cd backend && uv run python -m app.scripts.load_test --requests 200 --concurrency 20)
(cd backend && uv run python -m app.scripts.checkout_load --requests 30 --concurrency 10)
```

`load_test` / `checkout_load` 只用于本机开发基线，会留下 dev 数据，不构成容量结论。

## 测试方式

| 项目 | 命令 | 前置条件 |
|---|---|---|
| 后端单元 / 集成测试 | `(cd backend && uv run pytest -q)` | 无需外部服务（fixture 使用内存 SQLite） |
| 后端静态检查 | `(cd backend && uv run ruff check app tests)` | `uv sync` 已安装 dev 依赖 |
| 前端类型检查 / 构建 | `(cd frontend && npm run typecheck && npm run build)` | `npm install` 完成 |
| 前端 E2E | `(cd frontend && npm run test:e2e)` | 全栈已启动；本机安装 Edge（`channel: "msedge"`） |
| 任务级评测 | `(cd backend && uv run python -m app.scripts.task_eval)` | API 已启动且已 seed；默认店主账号 `owner@afterops.local` |
| 审计链校验 | `(cd backend && uv run python -m app.scripts.verify_audit)` | 数据库可连接 |

复跑记录（2026-11-23）：后端 `346 passed`（215.85s，1 条 Starlette 弃用警告），`ruff check app tests` 与前端 `tsc --noEmit` 通过。项目记录（2026-11-12）：任务级评测 19/19、Playwright E2E 44 条通过；这两项需要全栈运行，本次未复跑，实际结果以当前环境为准。

`task_eval` 是 HTTP 级评测，会创建带唯一手机号前缀的评测顾客 / 订单，不重置演示数据；报告默认写入 `reports/task-eval.md`。

## 数据、许可与安全

### 数据与语料

- `data/synthetic_shop/` 为人工构造的合成店铺数据，与真实商家、订单、快递单号无关。
- 仓库不附带、不镜像 JDDC 原始语料；`data/jddc/` 仅保留论文与许可取证、探测日志和合成样例。论文为 LREC 2020（CC-BY-NC，非商业），数据集条款未取得一手来源，按「待核实、不重分发」处理。

### 许可证

- 代码许可证：**待补充**。仓库当前没有 `LICENSE` 文件，许可证选择见 `docs/待补与歧义清单.md` Q-16（未定）。
- 第三方依赖与镜像遵循各自许可证；如需逐项清单：**待补充**。

### 安全

- 开发默认密码与密钥（`dev-only-change-me`、`owner123456` 等）只用于本地；生产必须在 `infra/.env.prod` 中全部替换。
- 仓库不含真实密钥、令牌或个人信息；提交前扫描记录见 `脱敏检查清单.md`。

## 旧原型（legacy）

根目录的 `src/`、`tests/`、`evals/`、`ops/`、`scripts/`、`requirements.txt`、`pytest.ini` 是 2026-10 的离线 Agent 原型：规则意图 + 确定性工具 + 双时态知识库 + 生成前合规门禁 + 哈希链审计 + 独立进程编排。它不连接 Postgres，也不参与当前全栈启动。

```bash
python -m venv .venv && .venv/Scripts/activate   # Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
copy .env.example .env                            # 根目录 .env.example 属于旧原型

python -m pytest tests/ -q
python -m src.shop.seed
python -m src.cli --demo --fresh-audit --rebuild
python -m src.cli --ask "你能保证全额退款吗"
python -m ops.cli check
python -m ops.cli start --smoke --port 18081
python -m ops.cli stop --port 18081
python -m evals.run_eval
```

旧原型的架构、意图体系、门禁映射与评测口径见 `docs/架构图.md`、`docs/意图体系.md`、`docs/GBT-47746义务-测试映射.md`、`docs/口径声明.md`、`docs/演示脚本.md`。Python 版本沿用旧原型说明（3.11+）。

## 贡献说明

- 本仓库为私有仓库，协作需由仓库所有者邀请；公开协作流程：**待补充**。
- 仓库未提供 `CONTRIBUTING.md`、Issue / PR 模板或 CI 配置，具体约定：**待补充**。
- 提交前至少执行：
  - `(cd backend && uv run pytest -q && uv run ruff check app tests)`
  - 涉及前端时：`(cd frontend && npm run typecheck && npm run build)`
  - 涉及界面 / 交易流程时：全栈启动后 `npm run test:e2e` 与 `task_eval`
- 现有提交信息使用中文描述；新增提交建议说明「改了什么、为什么、如何验证」。

## 文档索引

| 文档 | 内容 |
|---|---|
| [PRD v0](docs/product/PRD-v0.md) | 产品范围、逐版本能力与边界 |
| [90 天路线](docs/product/90-day-roadmap.md) | 当前阶段计划与验证口径 |
| [ADR-0001](docs/adr/0001-product-scope-and-stack.md) | 产品范围与技术选型决策 |
| [部署手册](docs/deployment.md) | 生产 Compose、TLS、备份与恢复 |
| [项目地图](项目地图.md) | 代码盘点（旧原型为主，注明新后端 / 前端并存） |
| [交付总结](交付总结.md) | 旧原型交付清单与验证记录（2026-10） |
| [reports/](reports/) | 各功能验证记录、任务级评测与压测输出 |

## 已知边界

- 支付为 mock PSP；未接真实支付渠道与结算。
- 短信未接入，OTP 仅在开发环境返回 `debug_code`。
- 物流节点由商家手工录入 / 推进，未接承运商 API 与面单。
- 邮件通过 SMTP 发送，开发环境使用 Mailpit；生产需配置真实 SMTP。
- 默认 AI 为确定性模板；Ollama / OpenAI 链路可配置，但模型质量、成本与线上效果未做量化评测。
- 单机 Compose 部署，不是高可用架构；数据库与上传卷为单点，未接 WAF / CDN / 对象存储。
- 限流基于数据库计数，不是分布式限流。
- 旧原型与当前全栈共用同一仓库，但数据层、启动方式与测试入口相互独立。