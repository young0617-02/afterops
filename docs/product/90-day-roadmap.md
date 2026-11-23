# 90 天技术路线 v0 —— 从现有 Agent 原型到可运营的售后系统

> 版本：v0.1 ｜ 日期：2026-11-02 ｜ 目标：第 90 天有 1 个真实商家每天使用，而不是一套演示
> 关联：[PRD v0](PRD-v0.md)、[ADR-0001](../adr/0001-product-scope-and-stack.md)、[项目地图](../../项目地图.md)

---

## 0. 目标与非目标

### 90 天结束时的验收

- 1 个真实商家（或 1 个设计伙伴）连续使用 ≥ 2 周。
- 真实商品/订单/物流/政策数据接入；不是纯合成脚本。
- 顾客能完成「浏览 → 加购 → 下单 → mock 支付 → 查订单 → 申请售后」。
- AI 能完成「查订单 → 引政策 → 提议退款/改地址 → 人工审批 → 执行 → 审计」。
- 红线：资金错误 0、错承诺 0、越权查询 0、审批绕过 0。
- 有可复跑的测试、任务评测、日志、指标、备份与恢复演练。

### 90 天不做

- 多租户、多仓库、多币种、优惠券、推荐、搜索集群。
- 真实支付/真实物流（首版 mock PSP；拿到资质再接沙箱）。
- 微服务、K8s、服务网格、事件总线集群。
- 移动端 App、小程序矩阵。
- 自动审批资金动作（首版全部人工审批）。

---

## 1. 默认技术决策（可改，代价见 ADR）

| 决策 | 默认 | 为什么 |
|---|---|---|
| 产品形态 | 单商家 3C 配件店 + AI 售后运营台；数据模型带 `merchant_id`，为 SaaS 留位 | 先验证切口，不先做多租户 |
| 架构 | **模块化单体**，一个后端进程 + 一个 worker + 一个 Next.js 前端 | 边界先于分布式；单人可维护 |
| 后端 | Python 3.12 + FastAPI + Pydantic 2 + SQLAlchemy 2 + Alembic | 复用现有 AI/门禁/审计积累，学习成本最低 |
| 数据库 | PostgreSQL 16+ | 事务、行锁、JSONB、FTS、pgvector 一个就够 |
| 前端 | Next.js（最新稳定版）+ TypeScript + 自定义 CSS | 店面、后台、聊天窗一套框架 |
| 队列 | 先 Postgres Outbox + worker；有指标后再上 Redis/RabbitMQ | 不提前引入中间件 |
| 支付 | 自建 mock PSP（支付单 + 签名 webhook + 幂等账本） | 不接真实资金也能练完整支付链路 |
| AI | 保留现有 provider 抽象（template/ollama/openai）；显式 tool-calling 循环 | 不引 LangChain，门禁和审计保持可控 |
| 部署 | Docker Compose + 单台 VPS + Caddy | 先跑起来，再谈 K8s |
| 可观测 | structlog + Prometheus 指标 + request_id；OpenTelemetry + Tempo 追踪 | 先能排错，再谈全链路追踪 |

**“不限制技术”在这里的含义**：不是每个层次都换新技术，而是不被现有 Python/SQLite 绑死；核心用 boring tech，边缘按需要实验。每引入一个新技术，必须回答：它解决哪个现有痛点、怎么验证、怎么回滚。

---

## 2. 目标仓库结构

先与现有 `src/` 并存，逐步迁移；不要一次性重写。

```text
ecommerce-support-agent/
├── backend/
│   ├── pyproject.toml
│   ├── alembic.ini
│   ├── alembic/                 # 迁移
│   ├── app/
│   │   ├── main.py              # FastAPI 入口
│   │   ├── api/
│   │   │   ├── store.py         # 商品/购物车/结算/订单
│   │   │   ├── admin.py         # 后台/审批/审计
│   │   │   ├── chat.py          # AI 会话
│   │   │   └── webhooks.py      # 支付/物流回调
│   │   ├── domain/
│   │   │   ├── catalog.py
│   │   │   ├── inventory.py
│   │   │   ├── orders.py        # 状态机
│   │   │   ├── payments.py
│   │   │   ├── aftersales.py
│   │   │   └── proposals.py     # 提案/审批/执行
│   │   ├── ai/
│   │   │   ├── orchestrator.py  # 对话编排
│   │   │   ├── tools.py         # 只读/提案工具
│   │   │   ├── gates.py         # 从 src/gates.py 迁移
│   │   │   ├── audit.py         # 从 src/audit.py 迁移/扩展
│   │   │   ├── policy.py        # 双时态政策
│   │   │   └── providers.py     # template/ollama/openai
│   │   ├── integrations/
│   │   │   ├── psp_mock.py
│   │   │   └── logistics_mock.py
│   │   └── worker/
│   │       └── outbox.py        # 可靠事件消费
│   └── tests/
│       ├── unit/
│       ├── integration/
│       ├── security/
│       └── e2e/
├── frontend/
│   ├── app/
│   │   ├── (store)/             # 店面
│   │   ├── (admin)/             # 后台
│   │   └── api/                 # BFF（如需要）
│   └── components/chat/         # 聊天窗
├── infra/
│   ├── docker-compose.yml
│   ├── Caddyfile
│   └── postgres/init.sql
├── docs/
│   ├── product/
│   └── adr/
├── src/                          # 现有 Agent 原型：先保留，逐模块迁移
├── evals/                        # 现有评测：保留；新增任务级评测
└── tests/                        # 现有测试：保留，确保迁移不回归
```

**迁移策略**
1. 先把 `src/gates.py`、`src/audit.py`、`src/intents.py`、`src/dialogue.py` 复制到 `backend/app/ai/`，改成可注入数据源。
2. 旧 `src/` 与新 `backend/` 并存；旧测试继续跑，确保行为不回退。
3. 新系统跑通后再删除旧实现；删除前必须有对应新测试。
4. 现有 `data/synthetic_shop` 变成种子/测试夹具；生产用 Postgres。

---

## 3. 数据模型与状态机

### 3.1 核心表（首版）

| 表 | 关键字段 | 约束 |
|---|---|---|
| `merchants` | id, name, status | 首版一行，但所有业务表带 merchant_id |
| `users` | id, merchant_id, email, password_hash, role, status | role: owner/agent |
| `customers` | id, merchant_id, phone, email, name | 支持访客 |
| `products` | id, merchant_id, sku, title, description, brand, specs, price_cents, status | `(merchant_id, sku)` 唯一 |
| `inventory` | product_id, warehouse_id, qty_available, qty_reserved, updated_at | `qty_available >= 0` |
| `carts` / `cart_items` | id, customer_id, product_id, qty, unit_price_cents | 同一购物车同一 SKU 合并 |
| `orders` | id, order_no, merchant_id, customer_id, status, payment_status, fulfillment_status, item_total_cents, freight_cents, discount_cents, payable_cents, currency, version, placed_at | 服务端计算金额 |
| `order_items` | order_id, product_id, sku, title_snapshot, qty, cancelled_qty, unit_price_cents | 下单快照与部分取消数量 |
| `payments` | id, order_id, provider, provider_payment_id, amount_cents, status, idempotency_key | `idempotency_key` 唯一 |
| `shipments` / `shipment_items` / `shipment_events` | order_id, package_no, order_item_id, qty, carrier, tracking_no, status, current_node, happened_at | 多包裹 mock |
| `after_sales_cases` | id, order_id, type, status, reason, amount_cents, proposal_id | type: refund/return/compensation/address_change |
| `action_proposals` | id, merchant_id, conversation_id, action_type, payload_json, risk_level, status, idempotency_key, created_by, approved_by, approved_at, executed_at, commerce_ref, error | **治理核心** |
| `policy_entries` | id, merchant_id, topic, title, body, version, valid_from, valid_until, source | 双时态 |
| `conversations` / `messages` | id, customer_id, channel, role, content, tool_calls_json, created_at | 会话留痕 |
| `audit_events` | id, merchant_id, conversation_id, proposal_id, event_type, payload_json, prev_hash, row_hash, created_at | 只追加 |
| `outbox` | id, event_type, aggregate_type, aggregate_id, payload_json, status, attempts, available_at | 可靠事件 |
| `idempotency_keys` | key, scope, response_json, created_at | API/webhook 幂等 |

### 3.2 关键 DDL 草图

```sql
CREATE TABLE orders (
  id              BIGSERIAL PRIMARY KEY,
  order_no        TEXT NOT NULL UNIQUE,
  merchant_id     BIGINT NOT NULL REFERENCES merchants(id),
  customer_id     BIGINT NOT NULL REFERENCES customers(id),
  status          TEXT NOT NULL,
  payment_status  TEXT NOT NULL,
  fulfillment_status TEXT NOT NULL,
  item_total_cents BIGINT NOT NULL CHECK (item_total_cents >= 0),
  freight_cents   BIGINT NOT NULL CHECK (freight_cents >= 0),
  discount_cents  BIGINT NOT NULL CHECK (discount_cents >= 0),
  payable_cents   BIGINT NOT NULL CHECK (payable_cents >= 0),
  currency        CHAR(3) NOT NULL DEFAULT 'CNY',
  version         INT NOT NULL DEFAULT 0,
  placed_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE action_proposals (
  id              BIGSERIAL PRIMARY KEY,
  merchant_id     BIGINT NOT NULL REFERENCES merchants(id),
  conversation_id BIGINT REFERENCES conversations(id),
  action_type     TEXT NOT NULL,          -- refund / address_change / compensation
  payload_json    JSONB NOT NULL,
  risk_level      TEXT NOT NULL,          -- L1 / L2 / L3
  status          TEXT NOT NULL,          -- pending / approved / rejected / executing / executed / failed
  idempotency_key TEXT NOT NULL UNIQUE,
  created_by      TEXT NOT NULL DEFAULT 'ai',
  approved_by     BIGINT REFERENCES users(id),
  approved_at     TIMESTAMPTZ,
  executed_at     TIMESTAMPTZ,
  commerce_ref    TEXT,
  error           TEXT,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE audit_events (
  id              BIGSERIAL PRIMARY KEY,
  merchant_id     BIGINT NOT NULL,
  conversation_id BIGINT,
  proposal_id     BIGINT,
  event_type      TEXT NOT NULL,
  payload_json    JSONB NOT NULL,
  prev_hash       TEXT NOT NULL,
  row_hash        TEXT NOT NULL,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

### 3.3 状态机

```text
order:
  pending_payment → paid → fulfilling → shipped → delivered → completed
  pending_payment → cancelled
  paid/fulfilling/shipped/delivered → refunding → refunded
  paid/fulfilling/shipped/delivered → partially_refunded

payment:
  created → pending → paid
  created/pending → failed
  paid → refunded / partially_refunded

after_sales_case:
  requested → reviewing → approved → executing → executed
  requested/reviewing → rejected
  approved/executing → failed

action_proposal:
  pending → approved → executing → executed
  pending → rejected
  pending → expired
  approved/executing → failed
```

**实现规则**
- 状态迁移只在 domain 层允许；数据库用 `CHECK` 约束收紧枚举。
- 订单/库存/支付更新带 `version` 乐观锁或 `SELECT ... FOR UPDATE`。
- 提案执行：先 `UPDATE ... WHERE status='approved' RETURNING` 抢占，再调用执行；同一 `idempotency_key` 只执行一次。
- 所有金额变更写 `audit_events`；审计链按 `merchant_id` 串接。

---

## 4. API 契约（首版）

统一错误体：`{"code": "...", "message": "...", "request_id": "...", "details": {...}}`
所有 POST 支持 `Idempotency-Key` 头；会话 Cookie 使用 `HttpOnly + SameSite=Lax`。

| 方法 | 路径 | 权限 | 用途 |
|---|---|---|---|
| GET | `/api/store/products` | 公开 | 商品列表/搜索 |
| GET | `/api/store/products/{sku}` | 公开 | 商品详情 |
| POST | `/api/store/cart/items` | 顾客 | 加购 |
| POST | `/api/store/checkout` | 顾客 | 创建订单 + 支付单 |
| POST | `/api/store/payments/{id}/mock-confirm` | 顾客 | mock 支付确认（仅开发/试点） |
| GET | `/api/store/orders` | 顾客 | 我的订单（订单号搜索 + 状态筛选 + 游标分页） |
| GET | `/api/store/addresses/last` | 顾客 | 最近订单收货地址（结算预填） |
| GET | `/api/store/orders/{id}` | 顾客（归属校验） | 订单详情 |
| POST | `/api/store/orders/{order_no}/confirm-receipt` | 顾客（归属校验） | 已签收订单确认收货 |
| GET | `/api/store/after-sales` | 顾客（归属校验） | 我的售后申请与状态 |
| GET | `/api/store/handoffs` | 顾客（归属校验） | 我的人工工单状态 |
| GET | `/api/store/notifications` | 顾客 | 我的站内通知与未读数 |
| POST | `/api/store/notifications/{id}/read` | 顾客（归属校验） | 标记通知已读 |
| POST | `/api/store/notifications/read-all` | 顾客 | 全部标记已读 |
| POST | `/api/store/after-sales/{id}/withdraw` | 顾客（归属校验） | 撤回待审售后申请 |
| GET | `/api/store/returns` | 顾客（归属校验） | 我的退货申请 |
| POST | `/api/store/returns` | 顾客（归属校验） | 对已签收订单申请退货（可带附件） |
| POST | `/api/store/attachments` | 顾客 | 上传 PNG/JPEG/WebP 凭证 |
| GET | `/api/store/attachments/{id}` | 顾客（归属校验） | 读取本人凭证 |
| GET | `/api/admin/attachments/{id}` | 客服/店主 | 读取商家凭证 |
| POST | `/api/store/returns/{id}/tracking` | 顾客（归属校验） | 登记回寄承运商/单号 |
| GET | `/api/admin/returns` | 客服/店主 | 退货列表与计数 |
| GET | `/api/admin/staff` | 店主 | 员工列表 |
| POST | `/api/admin/staff` | 店主 | 新增员工 |
| PATCH | `/api/admin/staff/{id}` | 店主 | 启停/调整角色 |
| POST | `/api/admin/staff/{id}/password` | 店主 | 重置员工密码 |
| GET | `/api/admin/outbox` | 客服/店主 | 邮件投递事件与失败原因 |
| POST | `/api/admin/outbox/{id}/retry` | 店主 | 失败邮件重新排队 |
| POST | `/api/admin/returns/{id}/approve\|reject` | 店主 | 审批退货 |
| POST | `/api/admin/returns/{id}/receive` | 店主 | 确认收货并生成退款提案 |
| GET | `/api/admin/notifications` | 客服/店主 | 客服站内通知与未读数 |
| POST | `/api/admin/notifications/{id}/read` | 客服/店主 | 标记通知已读 |
| POST | `/api/admin/notifications/read-all` | 客服/店主 | 全部标记已读 |
| POST | `/api/chat` | 顾客 | AI 会话（SSE 可选） |
| GET | `/api/chat/sessions/{id}` | 顾客（归属校验） | 会话历史 |
| GET | `/api/admin/overview` | 客服/店主 | 运营概览（待办 + 可选 24h/7d/30d 窗口） |
| GET | `/api/admin/overview/trend` | 客服/店主 | 近 N 天支付/退款/AI/提案/工单趋势 |
| GET | `/api/admin/products` | 客服/店主 | 商品列表（含草稿/下架） |
| GET | `/api/admin/products/{sku}` | 客服/店主 | 商品详情与仓库库存 |
| POST | `/api/admin/products` | 店主 | 新建商品 |
| PATCH | `/api/admin/products/{sku}` | 店主 | 编辑商品/改价/上下架 |
| PUT | `/api/admin/products/{sku}/inventory/{warehouse_id}` | 店主 | 设置仓库可售库存 |
| GET | `/api/admin/orders` | 客服/店主 | 履约订单列表（支持订单号/收货人/手机号搜索） |
| POST | `/api/admin/orders/{order_no}/shipment` | 店主 | 创建运单 |
| POST | `/api/admin/orders/{order_no}/shipment/advance` | 店主 | 推进物流节点 |
| GET | `/api/admin/handoffs` | 客服/店主 | 人工工单列表与计数 |
| GET | `/api/admin/handoffs/{id}` | 客服/店主 | 工单详情与会话记录 |
| POST | `/api/admin/handoffs/{id}/claim` | 客服/店主 | 接管工单 |
| POST | `/api/admin/handoffs/{id}/resolve` | 客服/店主 | 标记解决 |
| GET | `/api/admin/proposals` | 客服/店主 | 提案列表（状态/订单号/提案号搜索 + 游标分页） |
| POST | `/api/admin/proposals/{id}/approve` | 店主/主管 | 批准并执行 |
| POST | `/api/admin/proposals/{id}/reject` | 店主/主管 | 驳回 |
| GET | `/api/admin/audit` | 客服/店主 | 审计事件列表/筛选/分页 |
| GET | `/api/admin/audit/verify` | 客服/店主 | 哈希链完整性校验 |
| GET | `/api/admin/audit/export` | 客服/店主 | 审计事件 JSON 导出（含过滤条件与链状态） |
| POST | `/api/webhooks/psp/mock` | 签名 | 支付回调 |
| POST | `/api/webhooks/logistics/mock` | 签名 | 物流回调 |
| GET | `/health` | 公开 | 健康检查 |
| GET | `/metrics` | 内网 | Prometheus 指标 |

**AI 回复结构**

```json
{
  "request_id": "...",
  "reply": "...",
  "citations": [{"entry_id": "...", "version": "...", "valid_from": "...", "valid_until": "...", "source": "..."}],
  "proposals": [{"id": 123, "action_type": "refund", "status": "pending", "risk_level": "L2"}],
  "handoff": {"available": true, "queue": "after_sales", "ticket_ref": "..."},
  "confidence": 0.78,
  "degraded": false,
  "audit_id": 456
}
```

---

## 5. 分阶段计划

### Sprint 0（第 1–2 周）：发现 + 地基

**任务**
- 10 次商家访谈；确认 3 个设计伙伴；修订 PRD。
- 初始化 `backend/`、`frontend/`、`infra/`；Docker Compose 起 Postgres。
- Alembic 初始化；健康检查；CI（ruff + pytest + 前端 typecheck）。
- 种子数据：从 `data/synthetic_shop` 迁移 20–50 SKU、客户、订单、政策。
- 认证骨架：店主/客服登录；顾客会话。

**验收**
- `docker compose up -d` 后 `/health` 返回 200。
- `alembic upgrade head` 成功；`alembic downgrade -1` 可回滚。
- `pytest` 通过；`npm run typecheck` 通过。
- 至少 3 个商家愿意进入试点。

### Sprint 1（第 3–4 周）：商品与库存

**任务**
- 商品/SKU 表与 CRUD；后台商品页；店面列表/详情。
- 库存表与预留逻辑；下单预留、支付扣减、取消释放。
- 分类/品牌/搜索（Postgres FTS）。
- 图片先用占位/本地静态资源，不接对象存储。

**验收**
- 20–50 SKU 可浏览、可搜索、可上下架。
- 并发下单不超卖（`pytest` 并发用例）。
- 金额全部整数分；页面显示格式化。

### Sprint 2（第 5–6 周）：购物车、下单、mock 支付、订单

**任务**
- 购物车、地址、运费规则（首版固定/按区域）。
- 结算：服务端计算总额；创建订单 + 支付单。
- mock PSP：支付页、签名 webhook、幂等回调、账本。
- 订单状态机；后台订单列表/详情；顾客订单页。
- 发货 mock + 物流轨迹 mock。

**验收**
- 端到端：浏览 → 加购 → 结算 → mock 支付 → 订单已支付 → 发货 → 签收。
- 重复 webhook 不重复扣款/发货（幂等用例）。
- 订单金额与支付金额、账本三方一致。
- 后台可查订单与支付状态。

### Sprint 3（第 7–8 周）：AI 只读 + 政策 + 聊天 + 审计

**任务**
- 迁移 `gates/audit/intents/dialogue` 到 `backend/app/ai`；保留旧测试。
- 政策库 Postgres 化；双时态查询；引用版本/有效期。
- 只读工具：商品、订单、物流、政策、售后状态；归属校验。
- 聊天窗（店面嵌入）；SSE 流式；错误/降级提示。
- 审计：对话→工具→引用→回复；request_id 贯穿。
- 任务评测 v0：20 条只读用例 + 5 条越权 + 5 条注入。

**验收**
- 订单金额/状态/物流与数据库一致。
- A 顾客不能查 B 订单（越权 0）。
- 回复带政策引用；无现行政策时 fail-closed。
- 商品描述注入不改变工具调用（注入用例通过）。
- 审计可按 request_id 反查完整链路。

### Sprint 4（第 9–10 周）：提案、审批、售后、转人工

**任务**
- `action_proposals` + 审批后台；退款/改地址/补偿三类提案。
- 提案执行：批准后调用 mock 退款/改址；幂等；失败可重试。
- 人工转接队列；高危/低置信自动转；工单带上下文。
- 回复承诺扫描扩展：提案状态、金额、执行结果。
- 任务评测 v1：50 条（只读/提案/审批/越权/注入/幂等）。

**验收**
- AI 不直接调用执行接口（代码审计 + 测试）。
- 未批准提案不改变订单/资金。
- 批准只执行一次；重复批准/重放安全。
- 退款回复不含「已退款/一定退」；改址回复不含「已改好」直到执行完成。
- 审计链：对话→提案→审批人→执行→交易引用。

### Sprint 5（第 11–12 周）：试点 + 加固

**任务**
- 接入第 1 个设计伙伴的真实/脱敏数据（商品、订单、政策）。
- 连续运行 2 周；每日看指标；修高频问题。
- 可观测：结构化日志、Prometheus 指标、慢查询、错误率。
- 备份/恢复演练；迁移演练；密钥检查。
- 安全：限流、CSRF、CORS、Cookie、密码哈希、PII 脱敏。
- 压测：k6 50 并发只读 + 10 并发结算（目标先测，不承诺）。
- 写运维手册与事故复盘模板。

**验收**
- 商家连续使用 ≥ 2 周；AI 处理占比 ≥ 30%（或给出未达标原因）。
- 审批时长中位数 ≤ 10 分钟。
- 资金错误 0、错承诺 0、越权 0、审批绕过 0。
- 备份可恢复；迁移可回滚；p95 有实测数。
- 一份真实事故/问题复盘。

### Sprint 6（第 13 周）：复盘与下一步

**任务**
- 汇总指标：AI 占比、审批时长、错误、延迟、成本、留存、付费意愿。
- 复盘：哪些需求是真需求、哪些是自嗨；哪些技术债必须先还。
- 决策：继续当前切口 / 换类目 / 收缩为只读 / 扩到多租户。
- 更新 PRD v1 与下一阶段 90 天路线。

**验收**
- 一页指标报告 + 一页复盘 + 下一阶段路线。
- 不靠主观感觉做决策；每个结论带数据或访谈原话。

---

## 6. 测试与评测

### 6.1 测试金字塔

| 层级 | 工具 | 覆盖 |
|---|---|---|
| 单元 | pytest | 金额、状态机、权限、提案、政策 |
| 集成 | pytest + testcontainers | API + Postgres + 迁移 + webhook |
| 端到端 | Playwright | 浏览→下单→支付→订单→售后→审批 |
| 契约 | pytest | mock PSP/物流适配器 |
| 安全 | pytest | 越权、CSRF、注入、重放、限流 |
| 压测 | k6 | 只读、结算、聊天并发 |
| 评测 | 现有 judge + 新任务集 | 任务成功、引用、承诺、审批 |

### 6.2 必须有的测试用例

- 金额：分/元转换、优惠/运费、退款金额上限、四舍五入。
- 库存：并发下单不超卖；取消释放；退款回补规则。
- 支付：重复 webhook、乱序 webhook、签名错误、金额不符。
- 订单：非法状态迁移被拒；取消/退款分支正确。
- 提案：未批准不执行；批准只执行一次；驳回不执行；失败可重试。
- 权限：A 不能查 B 订单；客服不能越权审批大额；未登录不能访问后台。
- 注入：商品描述/评价/用户输入中的指令不触发写操作。
- 审计：篡改一行能被哈希链发现；导出可脱敏。
- 迁移：升级/回滚；备份恢复。

### 6.3 任务评测集（50 条）

| 类型 | 条数 | 例子 |
|---|---:|---|
| 订单/物流只读 | 15 | 金额、状态、轨迹、签收时间 |
| 政策问答 | 10 | 退换、保修、运费、时效 |
| 退款提案 | 10 | 有凭证/无凭证/超上限/已发货 |
| 改地址提案 | 5 | 未发货/已发货/已签收 |
| 越权 | 5 | 他人订单、猜订单号 |
| 注入 | 5 | 商品描述、评价、伪造政策 |
| 审批/幂等 | 5 | 重复批准、重放、驳回 |
| 支付/webhook | 5 | 重复回调、金额不符、签名错误 |

**红线**：错承诺 0、资金错误 0、越权 0、审批绕过 0。任何一条不为 0，停止扩功能，先修。

### 6.4 验收命令（目标形态）

```bash
# 基础设施
docker compose -f infra/docker-compose.yml config
docker compose -f infra/docker-compose.yml up -d --build

# 全栈入口
# web:  http://127.0.0.1:3000   api: http://127.0.0.1:8000

# 后端
(cd backend && uv sync && uv run pytest -q && uv run ruff check app tests)
(cd backend && uv run uvicorn app.main:app --host 127.0.0.1 --port 8000)

# 前端本地开发
(cd frontend && npm install && npm run typecheck && npm run build)
(cd frontend && npm run dev)

# 前端 E2E（需全栈已启动）
(cd frontend && npm run test:e2e)

# 任务级评测（需全栈已启动）
(cd backend && uv run python -m app.scripts.task_eval)

# 可观测
curl -s -D - -o /dev/null -H "X-Request-Id: drill-123" http://127.0.0.1:8000/health
curl -s http://127.0.0.1:8000/metrics | head
# Prometheus http://127.0.0.1:9090 / Alertmanager http://127.0.0.1:9093 / Grafana http://127.0.0.1:3001/d/afterops
# 本地告警接收：http://127.0.0.1:18080/alerts ；邮件收件箱：http://127.0.0.1:8025
# 追踪：Grafana → Explore → Tempo 数据源，按 trace_id 查询；Tempo API 仅容器内可达

# 备份恢复演练（临时库，不碰主库）
(cd backend && uv run python -m app.scripts.backup_drill)

# 轻量读压测（本机基线，不宣称容量）
(cd backend && uv run python -m app.scripts.load_test --requests 200 --concurrency 20)

# 并发结算探测（30 单 / 10 并发；会留下 dev 数据，不宣称容量）
(cd backend && uv run python -m app.scripts.checkout_load --requests 30 --concurrency 10)

# 开发/评测前清理限流（仅 dev）
(cd backend && uv run python -m app.scripts.clear_rate_limits)

# 数据库迁移（容器入口已自动执行；本机可手动跑）
(cd backend && uv run alembic upgrade head)

# 种子（会重建演示商家）
(cd backend && uv run python -m app.scripts.seed --reset)

# 审计与超时释放
(cd backend && uv run python -m app.scripts.verify_audit)
(cd backend && uv run python -m app.scripts.expire_reservations)

# 健康
curl -s http://127.0.0.1:8000/health
```

> 当前状态：全栈（web / api / worker / postgres + 观测栈）已可运行；后端 346 条测试 + 任务级 19/19 + 前端 44 条 Playwright E2E、typecheck/build 通过。
> 仍待补：生产级观测（外部告警渠道/采样与长期 Trace/指标存储）、PITR/跨机恢复、容量压测、真实模型与真实支付/物流。前端 E2E 与任务级评测已落地。旧原型 `python -m pytest tests -q` 仍保留可用。

---

## 7. 运维与安全

### 7.1 Docker Compose 服务

- `postgres`：16-alpine，数据卷，健康检查。
- `api`：FastAPI，迁移后启动，健康检查。
- `web`：Next.js，经 `API_BASE_URL` 代理到 api。
- `worker`：独立进程消费 `outbox`，通过 SMTP 发送客服邮件。
- `tempo`：Trace 存储，OTLP HTTP 4318，查询 3200，均仅回环。
- `prometheus` / `alertmanager` / `alert-sink` / `mailpit` / `grafana`：指标、告警、本地接收器与看板，均仅回环映射。
- 规划中：`caddy` 反向代理 + 自动 HTTPS（上线时）；outbox 拆分为多 worker/队列（当前单 worker）。
- mock PSP 当前与 api 同进程模块，减少服务数。

### 7.2 备份与恢复

- 每日 `pg_dump`；保留 7 天；异地/对象存储可选。
- 每月一次恢复演练：新库恢复 → 跑 smoke → 记录耗时。
- 迁移前自动备份；迁移脚本只前进，回滚用备份。

### 7.3 可观测

- 每个请求 `request_id`；日志 JSON；敏感字段脱敏；日志带 `trace_id`。
- 指标：请求数、错误率、p50/p95、支付回调失败、提案积压、审批时长、LLM 调用失败/成本。
- 追踪：OpenTelemetry → Tempo，本地按 trace ID 联查日志与 span。
- 告警（首版邮件/IM）：5xx 比例、支付回调失败、提案积压 > 10、备份失败。

### 7.4 安全清单

- 密码：argon2/bcrypt；不存明文。
- 会话：HttpOnly + Secure + SameSite；登出失效。
- 权限：RBAC + 资源归属；后台审批权限独立。
- CSRF：SameSite + token；CORS 白名单。
- 限流：登录、聊天、webhook。
- 密钥：`.env` 不入库；生产用密钥管理。
- 支付：签名校验、金额校验、幂等、重放防护。
- PII：手机号/地址脱敏；当前审计载荷不含手机号/地址，但含订单号/金额，JSON 导出未做额外脱敏。
- 注入：不可信内容隔离；工具白名单；红队用例。
- 依赖：锁版本；定期漏洞扫描。

---

## 8. 学习目标映射（产品即教材）

| Sprint | 技术主题 | 你会真正掌握 |
|---|---|---|
| S0 | HTTP、Docker、Postgres、迁移、CI | 环境可复现；迁移可回滚；测试门禁 |
| S1 | SQL、索引、事务、行锁、并发 | 不超卖；查询计划；约束与幂等 |
| S2 | 状态机、支付、webhook、金额 | 订单/支付一致性；重放安全；对账 |
| S3 | RAG、tool calling、引用、评测 | 事实溯源；政策版本；注入防御 |
| S4 | 工作流、审批、权限、审计 | 资金动作边界；并发执行；哈希链 |
| S5 | SRE、备份、压测、安全 | 故障恢复；容量；告警；事故复盘 |
| S6 | 指标、产品判断、路线 | 用数据决定做什么、不做什么 |

**建议阅读顺序**
1. PostgreSQL 官方文档：事务、隔离级别、锁、索引。
2. 《Designing Data-Intensive Applications》：一致性、复制、分区。
3. 《Release It!》：超时、重试、熔断、舱壁。
4. 《Site Reliability Engineering》：SLO、事故、容量。
5. 《Domain-Driven Design》：边界、聚合、领域事件。
6. 支付/电商官方文档：Stripe webhook、幂等、对账。

**学习方法**：每个主题必须落到一个可演示的系统行为 + 一个测试 + 一篇短设计记录（ADR/复盘）。只看书不写系统，不算掌握。

---

## 9. 风险与对策

| 风险 | 症状 | 对策 |
|---|---|---|
| 没有真实商家 | 做了 90 天没人用 | 第 1–2 周 10 次访谈；第 30 天设止损点；先做设计伙伴 |
| 范围膨胀 | Sprint 一直延期 | 非目标清单；每周砍需求；P0 之外不做 |
| 支付/合规 | 不敢接真实资金 | mock PSP 先跑；接真实前咨询合规与税务 |
| LLM 成本/延迟 | 商家不愿付 | 小模型 + 规则 + 缓存；模板兜底；按对话计量 |
| 安全漏洞 | 越权/注入/退款错误 | 权限矩阵 + 红队 + 审批 + 审计；上线前渗透测试 |
| 单人 burnout | 长期无反馈 | 每周可交付；先做只读+提案+审批；支付后置 |
| 技术动物园 | 维护成本高 | 核心一条栈；新技术必须解决现有痛点；可回滚 |

---

## 10. 第 91–180 天展望（不提前做）

只有 Sprint 5 指标达标才启动：

- **多租户**：merchant 隔离、套餐、计费、邀请。
- **真实支付**：Stripe test → 真实；或支付宝/微信沙箱 → 真实（看资质）。
- **真实物流**：快递查询 API、发货单、签收回调。
- **搜索/推荐**：Postgres FTS → pgvector → 独立搜索（只在需要时）。
- **数据分析**：售后原因、审批时长、AI 占比、成本。
- **开放能力**：MCP/插件，让商家把自己的系统接进来。

如果试点失败：不急着加功能，先换类目/换切口/收缩为只读工具；把失败写进复盘。

---

## 11. 当前状态与未验证边界

### 已完成（2026-11-02，Sprint 0.1 / 0.2 / Sprint 1-2 起步）

- `backend/` FastAPI 服务：`/`、`/health`、`/health/ready`（数据库就绪探针），Swagger 在 `/docs`。
- 30 张核心表：商家/用户/顾客/商品/库存/购物车/订单/支付/物流/售后/提案/退款/政策/会话/审计/工单/退货/退货政策/附件/通知/Outbox/幂等/Webhook/OTP/限流。
- Alembic 迁移：`b359d67fed81`（初始 schema）→ `b18114950b11`（商品目录字段）→ `7e2bce0c9fd5`（订单收货地址）→ `f8056bd3dd11`（退款账本）→ `aa8639caf35e`（OTP/限流）；真 Postgres 16 上升级/回滚验证通过。
- 模型/健康/目录/会话/购物车/结算/支付/订单/取消/部分取消/履约/多包裹发货/确认收货/自动完成/订单超时/订单详情/审计/审计锚点/工单/工单优先级/指派/SLA/坐席绩效/满意度/低分回访工单/首响超时升级/退货/退货政策/类目退货窗口/退货原因分析/退货运费/退货回补/退货质检/退货质检照片/退货质检扣减/逐件质检扣减/退货物流/退货物流异常/部分退货/逐件换货/聊天退货/退货自助/跨SKU换货/保修维修/维修寄回/商品保修/保修资格/首响提醒/话术模板/取消申请/商品评价/评价图片/商家回复/评价追评/优惠券/优惠券范围/领券中心/售后进度/通知/顾客邮件与偏好/邮件投递/超时提醒/概览/客户目录/财务对账/商品库存/商品批量导入/商品规格分组/库存流水/商品主图与详情图/低库存告警/库存对账/库存盘点/库存调拨/调拨单在途/库存导出/库存批量导入/库存估值/报损成本/人工客服回复/工单自动分配/工单内部备注/工单顾客上下文/坐席接单状态/坐席心跳/坐席技能路由/退款记录/净支付/配送方式/发票/发票作废/发票退款联动/工单搜索/低分告警/解决SLA/团队/地址簿/搜索分页/安全/CSRF/附件/政策/政策版本/帮助中心/对话/对话图片/坐席回复图片/聊天实时推送/退款进度/AI 售后事实查询/提案/人工退款/管理端/LLM 回退与门禁/OTP/限流/售后超时提醒/放弃补差换货/补差超时自动取消/售后收款台账/顾客售后风险画像/退货队列风险提示/退款渠道自动结算/退款失败自动重试/维修报价改价/维修配件成本/补差支付期限延期测试 **346 条**，`ruff` 通过。
- Seed：从现有 SQLite 合成店铺导入 Postgres——10 商品 / 17 库存 / 12 顾客 / 14 订单 / 16 明细 / 14 支付 / 9 运单 / 8 轨迹 / 4 售后单 / 16 条政策；金额对账 0 差异。
- 目录 API：`GET /api/store/products`（搜索/类目/分页）、`GET /api/store/products/{sku}`（含分仓库存）、`GET /api/store/categories`。
- 顾客会话：`POST /api/store/otp/request` → `POST /api/store/session`（手机号 + 6 位验证码）、`GET /api/store/me`、`DELETE /api/store/session`；HMAC 签名 Cookie（HttpOnly/SameSite）。
- OTP 与限流：验证码 5 分钟有效、最多 5 次尝试、单次使用；OTP/登录/管理端登录/聊天/结算均有限流，超限返回 429 + `Retry-After`。开发环境在 OTP 响应里返回 `debug_code`，生产不返回。
- 购物车：查询、加购、改数量、删除；库存校验；金额服务端计算。
- 结算：`POST /api/store/checkout`（要求 `Idempotency-Key`）；库存行锁预留、订单/支付单创建、幂等重放不重复下单。
- Mock PSP：`POST /api/store/payments/{id}/mock-confirm` + `POST /api/webhooks/psp/mock`（HMAC 签名）；支付成功提交预留、幂等。
- 并发锁顺序：结算/支付/取消/过期统一按「order → payment → inventory」加锁，避免反向锁导致死锁。
- 订单：列表/详情（含明细、支付、物流、地址）、归属校验；未支付取消释放库存；已支付未发货可申请整单取消，审批前禁止发货，批准后退款并回补原仓库存。
- 交易审计：结算/支付/取消/过期写入 `audit_events` 哈希链（`order_created` / `payment_paid` / `order_cancelled` / `order_expired`）；`python -m app.scripts.verify_audit` 可按商家校验。
- 政策库：`GET /api/store/policies`、`GET /api/store/policies/{topic}?as_of=YYYY-MM-DD`，双时态取现行版本，返回条目/版本/有效期/出处。
- 只读 AI 链路：`POST /api/chat`（需会话）→ 意图识别 → 订单/物流/政策/商品工具 → 带引用回复 → `chat_answered` 审计；会话与消息落库。
- 聊天历史：`GET /api/chat/conversations`、`GET /api/chat/conversations/{id}`；前端刷新/重开后恢复最近会话。
- AI 安全边界：高危/承诺索取只解释不确认；订单归属校验；无单号先追问；无现行政策 fail-closed；回复输出承诺扫描；每轮带人工出口。
- 退款进度：识别「退款到账/进度/状态/多久到账」等问题，读取订单最新售后提案，返回待审/已执行/驳回/失败、金额与处理引用；无申请时明确说明，不输出「已退款/保证到账」；报告 [reports/refund-status.md](../../reports/refund-status.md)。
- AI 售后事实：助手可查询退款账本、发票状态与需重开提示、维修寄回、退货回寄，回复带 `refunds:/invoice:/repair:/return:` 引用，不承诺到账或结果；报告 [reports/ai-after-sales-facts.md](../../reports/ai-after-sales-facts.md)。
- 说明：AI 回复默认是**确定性模板**；LLM 只能改写已批准回复，不能新增事实/数字/承诺。
- LLM 改写层：`template` / `ollama` / `openai`；候选回复出现承诺词、残留数字缺失、额外数字或丢失可执行的人工出口即回退模板，并记录 `llm_provider / degraded / fallback_reason` 到审计。
- 真实模型 smoke：本机 Ollama `qwen2.5:7b` 在本机进程与 API 容器（`host.docker.internal`）两条路径通过 2 条改写用例，商品数字与转人工出口均保留，`provider=ollama / degraded=false`；脚本 `uv run python -m app.scripts.llm_smoke`；默认仍为 `template`，OpenAI-compatible 未实测；报告 [reports/llm-smoke.md](../../reports/llm-smoke.md)。
- 提案与审批：`refund` / `compensation` / `address_change` 三类提案；AI 只创建 `action_proposals`，审批前不改订单/支付。
- 审批执行：`POST /api/admin/proposals/{id}/approve|reject`；退款/补偿写 `refunds` 账本，地址变更写订单地址；提案、售后单、审计同步更新。
- 人工售后动作：店主可代电话/线下顾客按订单创建退款 / 补偿 / 改地址提案（退款可部分金额、补偿受上限约束、改地址仅未履约订单），标记 `created_by=staff`，仍需本人在提案列表批准后执行；报告 [reports/manual-actions.md](../../reports/manual-actions.md)。
- 退款账本：新增 `refunds` 表（迁移 `f8056bd3dd11`），区分 `refund` / `compensation`，按 `proposal_id` 唯一，支持部分退款累计。
- 审批鉴权：管理端改为邮箱 + 密码登录（Argon2 哈希）+ HttpOnly 签名 Cookie；`owner` 可审批，`agent` 可查看提案、接管人工工单，但不能审批资金/地址提案。
- 演示管理员：`owner@afterops.local`，密码取 `DEMO_OWNER_PASSWORD`（默认 `owner123456`）；上线前必须替换。
- 容器入口实测：登录 → 加购 → 结算 → mock 支付 → 查订单；下单 → 取消 → 库存回补；政策现行/历史版本；聊天订单查询/政策引用/高危拒答/商品查询；**退款提案 → 店主登录 → 批准 → 退款执行 → 审计链 OK**；**店主创建运单 → 推进运输/派送/签收 → 客户订单页看到轨迹 → 审计链 OK**；旧 `X-Admin-Token` 访问返回 401。
- 前端：Next.js 16 店面（商品/搜索/类目/详情）、购物车、结算/沙箱支付、订单详情、聊天窗、商家运营台（运营概览 + 售后提案 + 人工工单 + 订单履约 + 商品库存 + 审计链）、顾客售后进度页、顾客/客服通知铃铛；Next 重写代理到 API，Cookie 同源。
- 全栈容器：`web` + `api` + `postgres`；`npm run typecheck` 与 `npm run build` 通过；`docker compose up -d --build` 后 `http://127.0.0.1:3000` 可用。
- 生产部署档：`infra/docker-compose.prod.yml` + Caddy + 强密钥环境模板；数据库/API/Web/worker 不暴露宿主端口，仅 Caddy 暴露 80/443；本地 `afterops.localhost` 内网 CA 验证 HTTPS、HSTS、Secure Cookie 与 `/metrics` 404；手册 [docs/deployment.md](../../docs/deployment.md)，报告 [reports/production-deploy.md](../../reports/production-deploy.md)。
- 生产观测：可选 overlay 接入 Prometheus/Alertmanager/Alert sink/Grafana/Tempo；实测 target up、4 条规则、双数据源、真实 trace；报告 [reports/production-observability.md](../../reports/production-observability.md)。
- 订单履约：店主后台创建运单（承运商/运单号），按 `待揽收 → 运输中 → 派送中 → 已签收/异常件` 推进；订单与履约状态同步、物流事件落库；客户订单页展示物流轨迹；`agent` 只读；运单号商家内唯一（迁移 `ac7cb534343a`）；每次操作写哈希链审计；顾客可在已签收后确认收货，订单进入 `completed`；worker 每小时自动完成超过 `ORDER_AUTO_COMPLETE_DAYS` 的已签收订单，跳过进行中退货/待审提案；报告 [reports/fulfillment.md](../../reports/fulfillment.md)。
- 审计台：后台「审计链」页签展示事件载荷、前后哈希与链完整性；支持按事件类型筛选、订单/运单/提案关键词搜索、分页加载与 JSON 导出，`agent` 可读；`GET /api/admin/audit/verify` 对整条链自洽校验，篡改单条事件可定位到首个断裂点；报告 [reports/audit.md](../../reports/audit.md)。
- 审计锚点：worker 定期把各商家审计链头用环境密钥 HMAC 签名后写入独立卷，后台展示锚点链/行匹配/链头覆盖；备份逐步归档 anchors 卷；报告 [reports/audit-anchors.md](../../reports/audit-anchors.md)。
- 人工工单：高危/承诺索取/显式转人工会创建持久化工单（`TK-000001` 形式），同一会话复用活动工单；工单按投诉/高风险自动分为紧急/高/普通，后台按优先级排序与筛选，升级写审计；后台可查看完整会话、接管、标记解决，解决后关闭会话；工单创建/接管/解决写审计；退款等提案走提案队列，不重复建工单；报告 [reports/handoff.md](../../reports/handoff.md)、[reports/handoff-priority.md](../../reports/handoff-priority.md)。
- 顾客售后进度：`/after-sales` 展示自己的退款/补偿/改地址申请状态、金额、原因、驳回说明、执行引用，以及人工工单状态；待审申请可撤回；仅能读取/操作本人数据；报告 [reports/after-sales.md](../../reports/after-sales.md)。
- 退货回寄：已签收订单可申请退货（退款或同款换货）→ 店主审批并返回退货地址 → 顾客登记回寄单号 → 店主确认收货：退款生成退款提案，换货生成零元替换订单并占用库存；全流程写审计并通知相关人员；报告 [reports/returns.md](../../reports/returns.md)。
- 聊天退货：聊天识别「我要退货/换货」等明确动作并校验归属与已签收状态，创建待审核退货申请，回复只说明等待店主审核、不承诺退款；聊天事务使用 savepoint，服务失败不会回滚会话消息；报告 [reports/chat-return.md](../../reports/chat-return.md)。
- 退货自助：顾客可撤回待审核退货（同订单可重新提交），并可追加最多 3 张凭证；后台待处理队列排除已撤回，状态映射与筛选补齐；迁移 `ef83416f6925`；报告 [reports/return-self-service.md](../../reports/return-self-service.md)。
- 退货政策：店主可设置全局可退天数（0–90）、类目窗口、退货运费、禁退类目与换货开关；资格与部分退货按件计算，帮助中心展示类目窗口；迁移 `580caaf644e6`、`b9c1e3d5f7a0`；报告 [reports/return-policy.md](../../reports/return-policy.md)。
- 退货原因分析：退货申请按六类原因归档，聊天关键词自动归类；后台可看原因分布、状态计数、Top 5 退货商品、批准率与平均审批时长；迁移 `e3a5b7c9d1f0`；报告 [reports/return-analytics.md](../../reports/return-analytics.md)。
- 政策版本管理：后台「政策」页展示现行版本与历史，店主可新增条目或发布新版本，上一版本自动闭合、变更写审计，AI 引用自动切到新版本；报告 [reports/policy-management.md](../../reports/policy-management.md)。
- 退货回补库存：确认收货时默认按原订单预留仓回补可售库存，可取消勾选；退款/换货共用逻辑，接口返回回补明细并写审计；报告 [reports/return-restock.md](../../reports/return-restock.md)。
- 部分退货：多商品订单可勾选部分商品退货，退款按所选商品小计减订单折扣分摊、运费仅整单退回时退；回补仅针对所选数量；迁移 `4d66f438a712`；报告 [reports/partial-returns.md](../../reports/partial-returns.md)。
- 退货运费：店主可配置 0–200 元固定运费并从退款中扣除；运费高于应退金额时无需退款；顾客/帮助中心展示，最终金额落入退货单与审计；迁移 `6235d4f181b6`；报告 [reports/return-shipping-fee.md](../../reports/return-shipping-fee.md)。
- 坐席绩效：团队页按 24h/7d/30d 展示每个账号的待处理、接管、解决、平均接管/解决耗时、接管 SLA 超时与全局未分配待接管；仅店主可见；报告 [reports/agent-performance.md](../../reports/agent-performance.md)。
- 满意度：已解决人工工单可由顾客提交 1–5 星与评论，一次评价写入审计；绩效表展示坐席 CSAT 与最近评价；迁移 `9c13ce47fd0d`；报告 [reports/satisfaction.md](../../reports/satisfaction.md)。
- 库存流水：结算预留、取消/超时释放、退货回补、换货占用、商品创建与手工调整统一记录变动量、结存、原因、关联单据与操作人；后台按 SKU 查询；迁移 `7036da747308`；报告 [reports/inventory-ledger.md](../../reports/inventory-ledger.md)。
- 库存对账：按流水累计校验已跟踪库存，后台展示账实一致/未跟踪/差异商品与差值；只读不改库存；报告 [reports/inventory-reconciliation.md](../../reports/inventory-reconciliation.md)。
- 库存盘点：店主按商品/仓库录入实盘数量，差异直接调整可售库存并写 `stocktake` 流水与审计，零差异只留审计；客服只读；报告 [reports/stocktake.md](../../reports/stocktake.md)。
- 库存调拨：店主可在仓库间即时调拨可售库存，双仓写 `transfer_out` / `transfer_in` 流水与一条审计，调入仓可自动建行；商品行锁串行化同商品并发调拨；真实 Postgres 反向并发探针通过；报告 [reports/inventory-transfer.md](../../reports/inventory-transfer.md)。
- 调拨单在途：跨仓调拨支持创建调拨单、调出仓即时扣减、在途可查、调入仓确认收货入库、取消回补调出仓；新增 `stock_transfers` 与 `transfer_cancel_in` 流水；迁移 `f9c1e3a5b7d2`；报告 [reports/stock-transfer-orders.md](../../reports/stock-transfer-orders.md)。
- 库存导出：当前库存快照与流水 CSV 下载，流水支持 SKU/仓库/日期过滤，UTF-8 BOM、中文原因与 CSV 公式注入防护；报告 [reports/inventory-export.md](../../reports/inventory-export.md)。
- 库存批量导入：店主按 SKU/仓库上传 CSV 覆盖可售数量，整批校验通过后单事务写库存、流水与审计，目标仓库可自动建行；报告 [reports/inventory-import.md](../../reports/inventory-import.md)。
- 人工客服回复：客服可在工单内直接回复顾客并自动接管，`agent` 角色消息进入原会话并通知顾客；人工处理期间 AI 暂停自动答复、保留投诉优先级升级，工单解决后恢复；迁移 `c7d2a4e91b30`；报告 [reports/agent-replies.md](../../reports/agent-replies.md)。
- 工单内部备注：店员可在工单内添加仅员工可见的备注，独立表存储、不进顾客会话、写审计，已解决工单也可补充；迁移 `b6d8f0a2c4e6`；报告 [reports/handoff-notes.md](../../reports/handoff-notes.md)。
- 工单顾客上下文：工单详情展示顾客历史订单/售后/待审/退货计数与最近 3 笔订单，复用客户目录聚合；报告 [reports/handoff-context.md](../../reports/handoff-context.md)。
- 坐席接单状态：店主/客服可手动切换「接单中/离开」，自动分配只选择接单中的客服并写审计；迁移 `c8e0f2a4b6d8`；报告 [reports/handoff-availability.md](../../reports/handoff-availability.md)。
- 首次响应 SLA：已接管且创建超过 30 分钟无客服回复的工单提醒接管人一次并写审计；坐席绩效增加首次响应数、平均首响与首响 SLA 超时；阈值 `HANDOFF_FIRST_RESPONSE_MINUTES`；报告 [reports/first-response-sla.md](../../reports/first-response-sla.md)。
- 坐席心跳：后台每 30 秒发送心跳，在线=启用+接单中+90 秒内；自动分配只选在线客服，团队页展示在线/离开/离线与最近心跳；迁移 `e7a9c1d3f5b8`；报告 [reports/agent-heartbeat.md](../../reports/agent-heartbeat.md)。
- 工单自动分配：店主可开启开关，新工单按在线、接单中且技能匹配的客服当前 `open/claimed` 数量选择负载最少者自动接管，未配置技能视为全部队列；写 `handoff_auto_assigned` 审计并通知本人；迁移 `f4b6d8e0c2a3`；报告 [reports/handoff-auto-assign.md](../../reports/handoff-auto-assign.md)。
- 坐席技能路由：店主可为客服勾选售后/投诉技能，自动分配只选择技能匹配的客服，无匹配保持待接管，技能变更写审计；迁移 `a0b2c4e6f8d1`；报告 [reports/agent-skill-routing.md](../../reports/agent-skill-routing.md)。
- 聊天图片：顾客每条消息最多附 3 张图片（文字与图片至少一项），AI 明确不识别图片内容并保留转人工，纯图片不自动建单；人工工单详情展示原图；迁移 `d9f1b3c5e7a0`；报告 [reports/chat-attachments.md](../../reports/chat-attachments.md)。
- 坐席回复图片：客服可在工单回复中附最多 3 张图片（文字与图片至少一项），附件按工单顾客归属并记录上传人，顾客聊天窗直接查看；迁移 `e0a2c4f6b8d1`；报告 [reports/agent-images.md](../../reports/agent-images.md)。
- 退货质检：确认收货时按退货明细选择回补库存或报损不入库，只有回补件写库存流水；报损记录质检人、质检时间与审计，顾客售后进度展示已回补/已报损；迁移 `f1b3d5e7a9c2`；报告 [reports/return-inspection.md](../../reports/return-inspection.md)。
- 库存成本与报损金额：商品维护成本价，后台按可售+预留数量计算库存估值；退货分析展示回补件数、报损件数与按当前成本价折算的报损成本；迁移 `b3d5f7a9c1e4`；报告 [reports/inventory-cost.md](../../reports/inventory-cost.md)。
- 退货物流：顾客登记回寄单号生成「已登记回寄」事件，店主按已揽收→运输中→派送中→已到达退货仓逐节点推进，每步通知顾客并写审计；到仓后仍需确认收货；迁移 `c4e6f8a0d2b5`；报告 [reports/return-tracking.md](../../reports/return-tracking.md)。
- 聊天 SSE：顾客会话新增 `text/event-stream` 流，按 `after_id`/`Last-Event-ID` 推送新消息；前端实时追加并显示「实时」标识，断线回退到 5 秒轮询；报告 [reports/chat-sse.md](../../reports/chat-sse.md)。
- 低分回访工单：1–2 星评价自动创建高优先级投诉队列工单并重开会话，关联原工单与评价，顾客收到回访单号，客服按现有队列接管回复；迁移 `d5f7a9c1e3b6`；报告 [reports/low-csat-followup.md](../../reports/low-csat-followup.md)。
- 逐件换货：多商品订单可按件换货，逐件指定同款或更低价目标 SKU，替换订单按目标商品合并生成，差价按件汇总为一个退款提案；迁移 `e6f8a0c2d4b7`；报告 [reports/multi-item-exchange.md](../../reports/multi-item-exchange.md)。
- 首响超时升级：已接管工单超过首响阈值仍无客服回复时，自动升级为高优先级、通知全部店主并写一次性审计，后台显示「SLA 已升级」；迁移 `a7c9e1b3d5f8`；报告 [reports/sla-escalation.md](../../reports/sla-escalation.md)。
- 退货物流异常：店主可在回寄途中登记异常说明，写入物流时间线、审计并通知顾客，不改变当前物流状态；仍可按节点推进；迁移 `b8d0f2a4c6e9`；报告 [reports/return-tracking-exception.md](../../reports/return-tracking-exception.md)。
- 评价追评：顾客可在原评价后 30 天内提交一次文字+最多 3 图追评，商品页/订单页/后台同步展示，隐藏评价同时隐藏追评；迁移 `c9e1b3d5f7a0`；报告 [reports/review-follow-up.md](../../reports/review-follow-up.md)。
- 领券中心：店主可将优惠券标记为可领取；顾客领取后进入券包，展示优惠码、剩余次数与状态，结算仍按码使用；迁移 `d0f2a4c6e8b1`；报告 [reports/coupon-claims.md](../../reports/coupon-claims.md)。
- 商品规格分组：商品可填规格组与规格名称，同组不同 SKU 独立价格/库存，详情页可切换同组规格并按规格搜索；迁移 `e1a3c5d7f9b2`；报告 [reports/product-variants.md](../../reports/product-variants.md)。
- 退货质检照片：店主可在确认收货前后上传最多 3 张质检照片，顾客与后台可查看，照片按退货顾客归属并写审计；迁移 `f2b4d6e8a0c1`；报告 [reports/return-inspection-photos.md](../../reports/return-inspection-photos.md)。
- 退货质检扣减：店主确认收货时可填写质检扣减金额与原因，退款提案按扣减后金额生成；扣减到 0 不生成提案，顾客与后台展示扣减依据；换货不支持；迁移 `c5e7a9b1d3f6`；报告 [reports/return-inspection-deduction.md](../../reports/return-inspection-deduction.md)。
- 逐件质检扣减：确认收货时可按退货明细分别填写扣减，退款按逐件合计计算，后台与顾客展示逐件金额；保留整单扣减兼容；迁移 `b1d3f5a7c9e2`；报告 [reports/return-item-deduction.md](../../reports/return-item-deduction.md)。
- 换货补发物流：换货替换订单生成后，补发运单、状态与轨迹进入退货视图，顾客售后页、后台退货卡与 AI 退货进度均展示补发包裹；补发发货/推进通知区分换货场景并跳转售后；报告 [reports/exchange-replacement-shipment.md](../../reports/exchange-replacement-shipment.md)。
- 退货扣减申诉：顾客可在收货后 7 天内对质检扣减提交一次申诉并上传最多 3 张凭证，待处理前可撤回；店主可按核准金额生成追加退款提案或填写说明驳回，后台支持「扣减申诉」筛选与计数，AI 可查询申诉状态；迁移 `c2e4f6a8b0d3`；报告 [reports/return-deduction-appeal.md](../../reports/return-deduction-appeal.md)。
- 退货上门取件：退货批准后可预约 7 天内取件时段，顾客/店主可取消；店主标记揽收后写取件单号与「已揽收」事件并进入回寄物流，后台支持「上门取件」筛选与计数；迁移 `d3f5a7c9e1b4`；报告 [reports/return-pickup.md](../../reports/return-pickup.md)。
- 工单会话摘要：人工工单自动生成诉求/订单/客户最近反馈/已回复/建议/会话规模的结构化摘要，AI 跟进与客服回复自动刷新，后台可手动刷新并写审计；规则模板、不调用生成式模型；迁移 `e4f6a8c0d2b5`；报告 [reports/handoff-summary.md](../../reports/handoff-summary.md)。
- 售后时间线：顾客售后页倒序聚合退货/回寄物流/上门取件/扣减申诉/维修/售后提案/退款补偿/发票/人工工单事件，按时间与订单号统一展示；只读聚合、无迁移；报告 [reports/after-sales-timeline.md](../../reports/after-sales-timeline.md)。
- 维修报价：维修件收货后店主可发送报价，顾客接受/拒绝并通过 mock 支付维修费，未确认或未支付不能完成维修；待确认或已拒绝时可改价重发，接受/支付后禁止改价；报价、改价、响应与支付写审计并通知双方；迁移 `d4f6a8c0e2b4`；报告 [reports/repair-quote-revision.md](../../reports/repair-quote-revision.md)。
- 退款渠道结算：退款执行后进入处理中；超过 `REFUND_AUTO_SETTLE_MINUTES`（默认 5 分钟）由 worker 自动标记渠道到账（mock `PSP-AUTO-`），财务仍可提前手动标记到账/失败并重试，顾客订单、AI 退款进度与售后时间线展示渠道状态；报告 [reports/refund-auto-settlement.md](../../reports/refund-auto-settlement.md)。
- 质检判责与报告：退货明细可填商家/顾客/物流/其他责任，商家责任禁止扣减；售后分析增加责任分布，店主可导出含判责的质检报告 CSV（公式前缀已转义）；迁移 `d8f0a2c4e6b9`；报告 [reports/return-responsibility.md](../../reports/return-responsibility.md)。
- 加价换货补差：换货目标价可高于原商品，收货按明细净差生成待支付补差或差价退款；补差支付前替换订单禁止发货，支付后可发货；迁移 `e9a1b3d5f7c0`；报告 [reports/exchange-supplement.md](../../reports/exchange-supplement.md)。
- 通知 SSE：顾客与后台铃铛通过 SSE 实时接收新通知并更新未读，按 after_id/Last-Event-ID 续传、10 秒心跳、25 秒短连接重连，保留 30 秒轮询兜底；无迁移；报告 [reports/notification-sse.md](../../reports/notification-sse.md)。
- 时间线补全与后台客户档案：售后时间线增加维修报价/维修费支付/换货补差支付/退款到账事件，后台客户档案可查看同一套售后时间线；无迁移；报告 [reports/timeline-admin-customer.md](../../reports/timeline-admin-customer.md)。
- 发票作废与重开：已开具发票可由店主填写原因作废，顾客收到通知并可重新申请，历史发票保留；作废与重开均写审计；迁移 `a2c4e6f8b0d3`；报告 [reports/invoice-void.md](../../reports/invoice-void.md)。
- 发票退款联动：退款后申请发票按净支付开票，已开票订单退款后后台与顾客展示「需重开」及发票金额/净支付对比，作废重开沿用人工流程；首次金额不一致时写审计并通知员工/顾客，财务发票队列可筛选「需重开」；报告 [reports/invoice-refund-adjustment.md](../../reports/invoice-refund-adjustment.md)、[reports/invoice-adjustment-notifications.md](../../reports/invoice-adjustment-notifications.md)。
- 话术模板：店主维护商家级共享话术并启停，客服在工单回复中选择模板填入输入框后编辑发送；模板变更写审计；迁移 `f7a9c1e3d5b8`；报告 [reports/quick-replies.md](../../reports/quick-replies.md)。
- 已支付取消：顾客对未发货订单申请整单取消并生成全额退款提案，审批前禁止创建运单；批准后退款并把 committed 预留回补原仓，驳回复恢发货；迁移 `a8b0d2f4e6c9`；报告 [reports/order-cancel-request.md](../../reports/order-cancel-request.md)。
- 聊天增量轮询：顾客聊天窗打开时每 5 秒按 `after_id` 拉取新消息，客服回复自动出现在当前会话并显示「客服」标识；发送后重新同步，避免乐观消息重复；无 WebSocket/SSE；报告 [reports/chat-polling.md](../../reports/chat-polling.md)。
- 配送方式：店主配置运费/包邮门槛，结算选择配送，订单保存运费；优惠券只抵商品，运费进入应付；迁移 `3b6ff8dd76ba`；报告 [reports/shipping-methods.md](../../reports/shipping-methods.md)。
- 工单搜索：后台按工单号、顾客手机号/姓名、最近消息搜索工单，解决队列按优先级限 50 条时找不到新单的问题；加载加请求序号防止旧响应覆盖搜索；报告 [reports/handoff-search.md](../../reports/handoff-search.md)。
- 质量告警：1–2 星评价通知员工并写审计；已接管超 24 小时未解决提醒一次，绩效新增解决 SLA 超时；报告 [reports/service-quality-alerts.md](../../reports/service-quality-alerts.md)。
- 发票：已支付订单可申请个人/企业发票，店主在财务页开票或驳回，顾客订单详情查看状态；迁移 `fe0ea55b3627`；报告 [reports/invoices.md](../../reports/invoices.md)。
- 保修维修：商品可配置 0–120 个月保修期（默认 12），结算时快照到订单明细；顾客申请维修前可查询每件商品资格与截止时间，签收后按快照判断，支持批准/驳回/回寄/收货/完成/维修寄回/顾客确认与撤回；维修不自动退款/换货；迁移 `b4fda5c9b198`、`d3e5b7c9a1f2`、`e6f8c0d2b4a7`、`d6f8b0c2e4a7`；报告 [reports/repairs.md](../../reports/repairs.md)、[reports/product-warranty.md](../../reports/product-warranty.md)、[reports/repair-return-shipping.md](../../reports/repair-return-shipping.md)。
- 商品评价：已签收订单逐件提交一次 1–5 星文字评价，可附最多 3 张图片；商品页展示平均分、评价与缩略图，店主可隐藏/恢复并可回复一次/编辑/清空，隐藏后公开图片不可读；迁移 `571815bce88b`、`c1d3e5f7a9b2`、`d2f4a6c8e0b1`；报告 [reports/product-reviews.md](../../reports/product-reviews.md)。
- 优惠券：店主创建固定/百分比券并启停，支持全场/类目/SKU 范围，顾客结算用码，按适用商品小计校验门槛与折扣；未支付订单取消或超时释放用量；迁移 `bda49e0f274e`、`a5c7e9b1d3f5`；报告 [reports/coupons.md](../../reports/coupons.md)。
- 帮助中心：`/help` 公开展示现行政策与退货规则摘要，支持搜索；聊天里的政策引用可直接跳到 `#policy-{entry_id}` 条文；报告 [reports/help-center.md](../../reports/help-center.md)。
- 售后凭证：退货申请可上传最多 3 张 PNG/JPEG/WebP 图片；待审退款提案也可由顾客补充凭证。校验文件头、大小，落本地卷，顾客/店主按权限读取；报告 [reports/attachments.md](../../reports/attachments.md)。
- 运营概览：后台首页汇总待审批提案、待接管/处理中工单、待处理退货、待履约订单、未读通知；支付/退款、AI 回复、提案与工单、审批中位耗时支持 24 小时 / 7 天 / 30 天窗口；趋势按日展示并可导出 CSV；附审计链完整性；报告 [reports/overview-range.md](../../reports/overview-range.md)。
- 安全：API/Web 增加 nosniff、DENY frame、Referrer/Permissions Policy 与 frame-ancestors CSP；带 Origin 的写请求做来源白名单；顾客/管理员会话分别下发绑定会话的 CSRF 令牌，写请求缺失或不匹配返回 403，webhook 与登录入口豁免，安全 GET 自动补发；`APP_ENV=prod` 时拒绝默认密钥启动；报告 [reports/security.md](../../reports/security.md)。
- 团队管理：店主可新增店主/客服、启停账号、调整角色、重置密码；自停用/自降级与最后一个启用店主会被拒绝；员工变更写审计；报告 [reports/staff.md](../../reports/staff.md)。
- 后台搜索：提案按订单号/提案号搜索与游标分页；履约订单按订单号/收货人/手机号搜索；顾客订单支持订单号搜索、状态筛选与游标分页；结算表单复用最近收货地址；报告 [reports/search.md](../../reports/search.md)。
- 地址簿：顾客可保存最多 10 个地址、设置默认、编辑/删除；首个地址自动默认，删除默认后自动提升；结算页可选择地址簿并回填；`/addresses/last` 默认地址优先、否则回退最近订单；迁移 `3647aa3cea0c`；报告 [reports/addresses.md](../../reports/addresses.md)。
- 客户目录：后台按手机号/姓名/邮箱检索客户，展示订单数、最近下单、售后数、待审提案、进行中退货；客户档案含最近订单、提案与退货；客服/店主只读；报告 [reports/customer-directory.md](../../reports/customer-directory.md)。
- 订单详情：履约页可查看单笔订单的商品行、支付流水、退款/补偿账本、多包裹运单与物流节点、关联提案与退货；客服/店主只读；报告 [reports/admin-order-detail.md](../../reports/admin-order-detail.md)。
- 订单拆包发货：店主可按订单明细逐件分配创建多个包裹，每个包裹独立运单与物流时间线；部分签收不提前完成订单，全部包裹签收且全部明细分配后才进入已签收；顾客、后台与 AI 物流回答按包裹展示；迁移 `a3c5e7b9d1f2`；报告 [reports/multi-package-shipping.md](../../reports/multi-package-shipping.md)。
- 未发货商品部分取消：顾客按件取消尚未分配包裹的商品，店主审批后按商品折扣分摊退款并回补原仓库存，订单继续履约剩余商品；已取消数量从发货、退货、维修与评价资格中扣除；迁移 `b4d6f8a0c2e4`；报告 [reports/partial-cancellation.md](../../reports/partial-cancellation.md)。
- 退款记录：顾客订单详情逐笔展示退款/补偿金额、状态、原因、处理引用与时间，汇总累计退款与净支付；补偿展示但不冲减净支付；报告 [reports/customer-refund-ledger.md](../../reports/customer-refund-ledger.md)。
- 财务对账：后台汇总支付毛额/退款/补偿/净额/待审资金提案，检查超额退款、已支付无流水、退款状态无账本三类异常，展示最近流水并导出 CSV；报告 [reports/finance-reconciliation.md](../../reports/finance-reconciliation.md)。
- 商品库存：店主可新建商品、编辑标题/类目/品牌/价格/描述/参数、切换草稿/在售/下架、按仓库设置可售库存；支持 CSV 批量导入商品与默认仓初始库存（整批校验、单事务）；客服只读；商品与库存变更写审计；低库存通知客服并写 outbox 邮件；报告 [reports/products.md](../../reports/products.md)、[reports/product-import.md](../../reports/product-import.md)。
- 商品主图与详情图：店主可上传/替换/移除主图，并可上传/删除最多 6 张详情图；文件头校验、5MB 上限、UUID 存储；店面列表用主图、详情页展示画廊；迁移 `746d6c845172`、`7bb24e5b40cf`；报告 [reports/product-images.md](../../reports/product-images.md)。
- 站内通知：提案创建/执行/驳回、工单创建/解决、运单创建与物流节点变化写入 `notifications`；顾客与客服顶部铃铛显示未读数，可逐条或全部已读，30 秒轮询；报告 [reports/notifications.md](../../reports/notifications.md)。
- 客服邮件：新提案/新工单在业务事务内写入 `outbox`，独立 worker 消费并通过 Mailpit SMTP 发送给启用客服账号；失败重试、超限置 `failed`，记录 `last_error`；后台「邮件投递」可查看并重新排队；报告 [reports/outbox-email.md](../../reports/outbox-email.md)。
- 顾客邮件：结算页或售后页可设置/停用通知邮箱，并分别开关发货/售后/退货三类邮件偏好；提案执行与驳回、退货、运单、工单解决、订单完成等顾客通知按偏好写入 outbox，worker 经 Mailpit 投递；邮箱按商家唯一、格式校验；报告 [reports/customer-email.md](../../reports/customer-email.md)。
- 顾客账户页：`/account` 汇总称呼、订单/售后/退货/地址数量、地址簿与最近订单售后；称呼经 `PATCH /api/store/me` 保存，手机号只读、通知邮箱跳转售后页管理；报告 [reports/account.md](../../reports/account.md)。
- 订单超时：worker 每 `ORDER_EXPIRE_INTERVAL_SECONDS` 扫描过期库存预留，取消未支付订单、回补库存、支付单置失败、写审计并通知顾客；实机首次启动清理 39 个历史遗留未支付订单；报告 [reports/order-expiry.md](../../reports/order-expiry.md)。
- 时效提醒：worker 定时扫描超过 `PROPOSAL_SLA_MINUTES` 的待审提案和超过 `HANDOFF_SLA_MINUTES` 的未接管工单，通知客服并写 outbox；按审计事件去重，只提醒一次；报告 [reports/sla.md](../../reports/sla.md)。
- 售后超时提醒：worker 每 `AFTER_SALES_REMINDER_INTERVAL_SECONDS` 扫描过期上门取件、超时未确认维修报价、超时未结算退款与待处理扣减申诉，按角色提醒一次并以 `reminded_at` 去重；阈值默认 24 小时；报告 [reports/after-sales-reminders.md](../../reports/after-sales-reminders.md)。
- 放弃补差换货：补差待支付时顾客或店主可取消换货，释放替换订单占用库存、关闭替换订单并按退货规则生成原订单退款提案；售后时间线与后台退货筛选新增「换货已取消」；报告 [reports/exchange-cancellation.md](../../reports/exchange-cancellation.md)。
- 补差超时与延期：收货时持久化补差截止时间并展示给顾客/后台；收货 24 小时提醒一次、到期后 worker 自动取消换货（释放库存、生成退款提案）；店主可在超期前按 1–72 小时延期，写审计并通知顾客；报告 [reports/exchange-supplement-expiry.md](../../reports/exchange-supplement-expiry.md)、[reports/exchange-supplement-extension.md](../../reports/exchange-supplement-extension.md)。
- 售后收款台账：换货补差与维修费支付后写入独立收款台账，财务页展示分类合计、明细与周期汇总，CSV 包含两类收款，并回填历史已支付记录；不改变订单应付与退款口径；报告 [reports/after-sales-charges.md](../../reports/after-sales-charges.md)。
- 顾客售后风险画像：客户档案按 90 天窗口展示退货次数/退货率、顾客主动取消补差换货、扣减申诉与顾客责任次数，按固定阈值给出高/需关注/正常与可核对原因；后台退货队列内联展示风险徽章与原因（批量计算），顾客端不返回画像；只读提示，不影响审批与分配；报告 [reports/customer-risk-profile.md](../../reports/customer-risk-profile.md)、[reports/return-risk-hint.md](../../reports/return-risk-hint.md)。
- 退款自动结算与重试：worker 将超过 5 分钟的渠道处理中退款自动标记到账（mock `PSP-AUTO-`）；失败超过 15 分钟自动重试最多 3 次，写审计并通知顾客；财务手动结算与失败重试保留，阈值可关闭；报告 [reports/refund-auto-settlement.md](../../reports/refund-auto-settlement.md)、[reports/refund-auto-retry.md](../../reports/refund-auto-retry.md)。
- 维修报价改价：待顾客确认或顾客已拒绝的维修报价可改价重发，重置提醒与响应状态、写 `repair_quote_revised` 审计并通知顾客；接受或支付后禁止改价；时间线展示更新后的报价与金额；报告 [reports/repair-quote-revision.md](../../reports/repair-quote-revision.md)。
- 维修配件成本：完成维修时可记录配件成本与说明（顾客端不可见），后台维修卡展示成本/毛利，财务售后收款展示维修成本与毛利合计；一次性录入，无配件明细/库存扣减/工时；报告 [reports/repair-parts-margin.md](../../reports/repair-parts-margin.md)。
- Playwright E2E：`npm run test:e2e`，44 条用例：购买/退款提案/店主批准/历史恢复 + 概览/审计链完整性 + 顾客售后进度与通知、取消未支付订单/已支付取消申请与发货拦截、顾客按件取消未发货商品并由店主批准后只发剩余商品、顾客查看退款记录与净支付/聊天查询退款账本、购买/发货/派送/签收/客户轨迹/确认收货、订单拆两包分别签收与顾客查看两包、商品评价（含图片、商家回复与追评）、转人工建单/客服接管/客服会话回复/工单内部备注/工单会话摘要与刷新/顾客上下文/话术模板套用/解决/常开聊天窗 SSE 实时回复/通知铃铛 SSE 实时未读/聊天图片双向发送与工单查看、顾客评价工单 + 团队绩效 CSAT + 低分自动回访工单、店主指派工单 + 顾客工单状态与通知、投诉工单紧急优先级、客户目录检索与售后时间线、重复退货客户高风险管理画像、顾客撤回待审售后、退货回寄到退款与回补库存（原因分类/分析面板/质检扣减/逐件扣减与顾客查看依据/扣减申诉与店主核准追加退款/预约上门取件与店主揽收/售后统一时间线）、多商品订单只退一件、登记退货物流异常并继续推进到仓、上传质检照片、按件报损、逐件责任判定与质检报告导出、多商品订单只换一件并逐件指定换货 SKU/替换订单补发后顾客售后与后台展示补发运单/加价换货支付补差后发货/高价换货顾客放弃补差并由店主取消换货、释放库存与生成退款提案/店主延期补差支付期限、店主设置退货政策（全局窗口/类目窗口/运费/禁退类目）与顾客逐件资格提示、店主发布政策版本与查看历史、聊天申请退货、顾客撤回待审退货、保修维修申请（含保修资格提示）到完成并登记寄回运单、发送维修报价、顾客拒绝后改价重发并由顾客接受/支付、完成时记录配件成本与毛利、顾客确认收到、店主手动退款并标记渠道到账（含自动结算/重试提示）、店主新增并停用员工、查看坐席绩效（含首响列）、自动分配设置与接单状态切换/团队在线状态与最近心跳/客服技能勾选与徽章、店主驳回、agent 提案只读、店主新建商品（成本价/规格组）/顾客商品详情切换规格/CSV 导入商品/上传主图与详情图/设置保修期/查看库存成本估值/调库存/盘点/跨仓调拨/创建并收取在途调拨单/导出库存与流水/批量导入库存并查看对账到店面、地址簿与结算回填、店主创建全场/类目优惠券、顾客在领券中心领取并结算用码、店主创建配送方式并顾客付运费、顾客申请发票、店主开票、作废并顾客重新申请、开票后退款显示需重开与净支付/需重开筛选与顾客通知、通知邮箱、顾客账户称呼编辑、帮助中心搜索与引用跳转、大量积压工单下按工单号搜索、移动端布局；全部通过。
- 任务级评测：`uv run python -m app.scripts.task_eval`，19/19 通过（商品/政策/购买/取消/退款批准/手动退款/手动补偿/退款进度/撤回/退货/聊天退货/同款换货/跨SKU换货/退款驳回/改地址/越权/高危拒答/注入/限流）；报告 [reports/task-eval.md](../../reports/task-eval.md)。
- 可观测：每个请求返回/贯穿 `X-Request-Id`；`GET /metrics` 输出 Prometheus 文本（请求计数 + histogram）；容器 `LOG_FORMAT=json` 输出结构化请求日志。
- 监控栈：Prometheus 抓取 `afterops-api`（up）+ 4 条告警规则；Grafana 13.3 自动配置 Prometheus 数据源与 `AfterOps · API` 看板；Docker 日志 10MB×3 轮转。
- 告警投递：Prometheus → Alertmanager 0.34.2 → webhook sink + Mailpit 邮件（1.33.0）；断流演练 firing/resolved 均到达 sink；邮件告警可在 Mailpit 收件箱核对；恢复后 E2E 通过。
- 分布式追踪：API 接入 OpenTelemetry，经 OTLP HTTP 上报 Tempo（运行时 v3.0.0）；Grafana 配置 Tempo 数据源，可按 trace ID 查询；JSON 日志携带 `trace_id`。
- 追踪实测：示例 trace `2682cabb1c0488467ce983a3bebc11f7`（`GET /health`）含 4 个 span，Tempo 与 Grafana 代理查询均 200；接入追踪后后端 128 测试与前端 11 条 E2E 复跑通过。
- 观测报告：[reports/observability.md](../../reports/observability.md)。
- 备份恢复演练：`uv run python -m app.scripts.backup_drill`，`pg_dump` → 临时库恢复 → 30 张表/计数校验 → 删除临时库；同时归档 uploads 卷到 tar.gz，并在临时目录校验附件 SHA-256；报告 [reports/backup-drill.md](../../reports/backup-drill.md)。
- 生产备份：生产 Compose 增加 `backup` sidecar，默认每日 `pg_dump` + uploads 归档、SHA-256、7 天保留清理；支持 `BACKUP_ONCE=1` 单次执行；隔离生产栈实测归档校验与旧文件清理；报告 [reports/backup-automation.md](../../reports/backup-automation.md)。
- 轻量读压测：`uv run python -m app.scripts.load_test --requests 200 --concurrency 20`，200/200、错误 0；本机基线 p50 141.0ms / p95 344.0ms / p99 406.0ms；报告 [reports/load-test.md](../../reports/load-test.md)。
- 并发结算探测：`uv run python -m app.scripts.checkout_load --requests 30 --concurrency 10`，30/30 成功、库存 80→50 无超卖；本机 p50 1094ms / p95 1625ms / p99 1672ms；报告 [reports/checkout-load.md](../../reports/checkout-load.md)。发现并修复并发死锁：checkout/payment 同商家 advisory lock 串行化 + 死锁重试。
- 审计链并发追加：原 `SELECT ... FOR UPDATE` 锁商户行会与外键 `KEY SHARE` 形成死锁（任务评测手动退款曾 500）；改为两键 advisory lock 串行化后，探针 2 并发追加通过、临时还原行锁可复现死锁；报告 [reports/audit-lock.md](../../reports/audit-lock.md)。
- 浏览器实测（msedge 1440×1000 / 390×844）：登录 → 加购 → 结算 → 支付 → 聊天退款提案 → 店主批准 → 订单变已退款；购买 → 后台创建运单 → 运输/派送/签收 → 客户订单页轨迹；转人工 → 客服接管/解决；移动端布局可用。

### 未完成（按 Sprint 推进）

- 真实外部告警/邮件渠道（生产 SMTP/IM/电话）、生产 Trace 采样与长期存储、长期指标存储、PITR/跨机恢复、容量压测（仅 30/10 本地探测）。
- 安全：CSRF 令牌按会话派生但不按请求轮换、不绑定设备；已提供 Caddy 生产部署档并在本地 TLS 验证，但未接真实域名/ACME 证书、WAF/渗透测试/依赖审计；CSP 仅 frame-ancestors/base-uri。
- OpenAI-compatible 真实服务未实测；Ollama 仅完成 2 条改写 smoke，未做质量、延迟、并发或成本评测。
- 真实短信服务商接入（当前 OTP 为本地 debug_code）；管理端 MFA。
- 真实支付与真实物流承运商 API（当前为 mock PSP；发货/节点由商家手工录入，未接快递查询或签收回调）。
- 审计外部存证：已有本地 JSON 导出与独立卷 HMAC 签名锚点，但未接 WORM/第三方时间戳/非对称外部签名；拥有主机 root 权限者仍可删除锚点文件。
- 人工工单：支持店主手动指派/改派与客服会话回复，客服首次回复自动接管；仍未接短信/IM，聊天与通知已走 SSE 实时推送（保留轮询兜底）；已有普通/高/紧急分诊、优先级排序筛选、可开关的负载最少自动分配（跳过离开、心跳过期与技能不匹配客服）、5/10/15 分钟分层接管 SLA 与 30 分钟首次响应提醒；有站内队列 + 客服邮件；仍无技能等级/权重、自定义队列、排班与在线时长统计。
- 换货支持逐件跨 SKU（含加价补差与差价退款）；补差待支付时顾客或店主可取消换货，收货 72 小时仍未支付由 worker 自动取消并生成原订单退款提案；替换订单为零元订单、走现有履约发货；仍无二次催付、人工延期、改价重报、退回原商品、部分明细取消换货、退货物流 API 与自动质检规则；退货地址为配置值，退款仍走单独审批。
- 顾客可撤回待审退款/补偿/改地址提案与待审退货，可补充退货凭证；不支持修改退货原因/处理方式或撤回已审批申请；状态变化通过站内通知 SSE（30 秒轮询兜底）与售后时间线提示，无顾客短信/IM/Web Push。
- 运营概览为滚动窗口（24 小时 / 7 天 / 30 天），不是自然日/自然月账期；趋势 CSV 在浏览器生成；无利润/COGS/税费口径，库存估值与报损金额按当前成本价而非历史批次成本；售后补差/维修费已有独立收款台账，但不进入订单支付毛额、净额与发票口径；开发/评测数据会抬高读数。
- 商品支持 CSV 批量导入但不更新已有 SKU、不导入描述/规格/图片；仍无视频、条码、供应商/采购单或安全库存预警；已有调拨单与在途状态，但无调拨审批流/批次成本；图片为本地文件（1 主图 + 6 详情图），无对象存储/CDN/图片审核/排序；库存为可售数量直接设置，不自动补货。
- 售后凭证仅退货流程与图片；无病毒扫描、内容审核、对象存储/多副本、缩略图或附件删除。
- 生产备份已每日落本地备份卷并保留 7 天，含 uploads 归档与哈希校验；未接异地/对象存储、PITR 或自动恢复附件到线上卷；`pg_dump` 与 tar 归档需一起保存。
- 顾客/客服邮件均走本地 Mailpit；无顾客短信/IM/Web Push；顾客只有发货/售后/退货三类粗粒度邮件偏好，无单事件订阅；客服通知按启用账号逐人落库。
- 团队管理无邀请邮件、员工自助改密、细粒度权限或操作审计导出；角色仍只有 owner/agent。
- 邮件投递单 worker、无多 worker 调度；重试需人工触发，失败原因只保留最近一次（截断 1000 字符）。

### 未验证（不得写成结论）

- 商家需求与付费意愿、AI 处理占比、审批时长、成本、并发能力、真实支付/物流。
- 所有性能数字是设计目标；Sprint 5 后回填实测。

---

## 12. 决策记录

默认决策及其代价见 [ADR-0001](../adr/0001-product-scope-and-stack.md)。任何改动（换类目、换栈、接真实支付、上多租户）都先在 ADR 里写清楚：为什么改、影响面、回滚方式。
