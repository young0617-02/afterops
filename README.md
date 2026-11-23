# AfterOps · AI 售后运营台

> 当前主线（2026-11 起）：面向 3C 配件的售后运营台——完整交易底座（商品/库存/支付/履约/售后）+ AI 只查事实/只提案 + 店主审批/人工工单 + 哈希链审计。
> 全栈入口：`docker compose -f infra/docker-compose.yml up -d --build` → web `http://127.0.0.1:3000` / api `http://127.0.0.1:8000`。
> 观测入口：Prometheus `http://127.0.0.1:9090` / Alertmanager `http://127.0.0.1:9093` / 本地告警接收 `http://127.0.0.1:18080` / 邮件收件箱 Mailpit `http://127.0.0.1:8025` / Grafana `http://127.0.0.1:3001`（均仅回环；Grafana 本地 admin/admin，匿名 Viewer；Grafana 内已接 Tempo 追踪，按 trace ID 查询）。
> 产品文档：[PRD v0](docs/product/PRD-v0.md) · [90 天路线](docs/product/90-day-roadmap.md) · [ADR-0001](docs/adr/0001-product-scope-and-stack.md) · [项目地图](项目地图.md)
> 本地管理员：`owner@afterops.local` / `DEMO_OWNER_PASSWORD`（默认 `owner123456`）；客服：`agent@afterops.local` / `DEMO_AGENT_PASSWORD`（默认 `agent123456`，可查看提案、接管人工工单，不能审批资金/地址）；本地 OTP 验证码在登录框旁显示，生产不返回。
> 当前边界：mock PSP、默认模板 AI（Ollama `qwen2.5:7b` 已实测，可切 openai）、OTP debug_code、物流为商家手工录入运单/节点、客服与顾客邮件均走本地 Mailpit；未接真实短信/支付/承运商 API。

## 快速开始

```bash
# 全栈（web + api + worker + postgres + 观测栈）
docker compose -f infra/docker-compose.yml up -d --build

# 生产部署（Caddy TLS，见 docs/deployment.md）
# cp infra/.env.prod.example infra/.env.prod 并替换密钥后：
# docker compose -p afterops-prod -f infra/docker-compose.prod.yml --env-file infra/.env.prod up -d --build

# 后端测试 / 任务级评测（需全栈已启动）
(cd backend && uv run pytest -q)
(cd backend && uv run python -m app.scripts.task_eval)

# 可选：真实本地模型改写 smoke（需 Ollama 与对应模型）
(cd backend && LLM_PROVIDER=ollama OLLAMA_MODEL=qwen2.5:7b uv run python -m app.scripts.llm_smoke)

# 前端类型检查 / E2E（需全栈已启动）
(cd frontend && npm install && npm run typecheck)
(cd frontend && npm run test:e2e)

# 备份恢复演练 / 轻量读压测
(cd backend && uv run python -m app.scripts.backup_drill)
(cd backend && uv run python -m app.scripts.load_test --requests 200 --concurrency 20)
(cd backend && uv run python -m app.scripts.checkout_load --requests 30 --concurrency 10)
```

---

# 早期原型（保留供参考与旧测试）

**电商导购/客服 Agent —— 合规门禁原型（JDDC 公开语料口径 + GB/T 47746 门禁）**

**一句话**：按 2026-09-01 实施的 GB/T 47746—2026，把「资损门禁」与「转人工」做成**可审计、可测试**的机制，
并用公开真实语料口径做离线评测。

**它是什么**：单人自主完成的求职作品集原型。**不是产品、不是商城、不接真实店铺 IM、没有真实流量**，
店铺数据全部合成。承诺类结论一律不由 AI 作出。

---

## 1. 为什么做这个（缺口不在「能不能答」，在「答错了谁负责」）

- 行业口径：绝大多数卖家已用 AI，但真正部署「能自主执行全链路 + 人审决策」的 Agent 是少数；
  同期 AI 客服被消费者协会点名为投诉新热点，售后类投诉占比居首。（数字来源与年份见
  `docs/口径声明.md` 第四节，全部标注为外部对照，不进入本项目指标。）
- 反面基线：海外有厂商把「用户沉默即视为已解决」计为解决并按结果收费。
  **本项目明确不使用该口径**，并把它写成红线测试里的一条禁止项（见 `tests/test_no_false_claims.py`）。
- 与平台件抢「答得快」没有空间；抢「答得可审计、错承诺可拦在输出之前」是空白格，
  而且有刚生效的国标条文可以逐条对表。

## 2. 三十秒看懂它做到哪

| 国标义务（本项目落地口径） | 机制 | 可执行的证据 |
|---|---|---|
| 重大事项只解释不确认 | 高危意图（退款/赔偿/价保/投诉）只输出规则解释 + 条文出处，确认动作强制转人工 | `pytest tests/test_obligation1_explain_only.py -q` |
| 人工入口不得隐藏 | 每轮必带人工出口；紧急词命中自动转接；出口补不齐就抛错不发送 | `pytest tests/test_obligation2_human_entry.py -q` |
| 承诺白名单，输出**前**拦截 | 承诺类别不进入生成器输入（第一道）+ 输出侧词典与数字溯源复核（第二道） | `pytest tests/test_obligation3_commitment_whitelist.py -q` |
| 留痕可溯 | 幂等键 + 决策链 + 条文版本 + 工具证据 + 哈希链可自检 | `pytest tests/test_obligation4_audit_trace.py -q` |

完整对照表（含 A1–A14 验收项的跑法与实测结果）：**[`docs/GBT-47746义务-测试映射.md`](docs/GBT-47746义务-测试映射.md)**

## 3. 快速开始（离线，零密钥，30 分钟内可跑完）

```bash
python -m venv .venv && .venv/Scripts/activate        # Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
copy .env.example .env                                 # 全部默认值即可运行（Python 3.11+）

python -m pytest tests/ -q                             # ① 测试套件
python -m src.shop.seed                                # ② 合成店铺库 + 内容哈希（可复现证明）
python -m src.cli --demo --fresh-audit --rebuild       # ③ 11 轮演示：金额溯源/承诺拦截/自动转人工
python -m src.cli --ask "你能保证全额退款吗"            # ④ 单轮试问
python -m ops.cli check                                 # ⑤ 服务预检（不动进程）
python -m ops.cli start --smoke --port 18081             # ⑥ 一键启动 + 一发真请求验证
python -m ops.cli stop --port 18081                      # ⑦ 一键停止（端口释放 + 无孤儿校验）
python -m evals.run_eval                               # ⑤ 170 条四档评测 + 生成报告
python scripts/mutation_test.py --report reports/变异测试.md   # ⑥ 注入缺陷，验证测试有鉴别力
```

交互式会话：`python -m src.cli`（同一 `--session` 内有多轮上下文与幂等重放）。
演示逐轮讲解与手工步骤核对：[`docs/演示脚本.md`](docs/演示脚本.md)。

### 3.1 一键启动 / 一键停止（服务编排）

```bash
python -m ops.cli check            # 只预检：端口 / provider 配置 / 目录可写，不动任何进程
python -m ops.cli start            # 已在运行 → 先按逆序全部停止 → 再按依赖顺序启动 → 逐个健康确认
python -m ops.cli start --smoke    # 启动后再打一发 /health + /v1/chat + /v1/audit，确认「进程在」=「能答」
python -m ops.cli stop             # 优雅停止：服务自机关闭 → 等在途 → 超时才升级强杀 → 三项残留校验
python -m ops.cli stop --force     # 服务卡死时直接回收整棵进程树
python -m ops.cli status           # 未运行 / 运行中(PID) / 状态文件残留 / 健康详情
python -m ops.cli logs api -n 50   # 日志尾部（子进程 stdout+stderr 合并在一处）
```

停止流程一定校验三件事：**进程真没了（无孤儿）、端口真释放了、状态与停机令牌文件真清掉了**——
只在嘴上说「已停止」不算完成。服务侧收尾顺序：停止接新请求 → 等在途请求归零（`DRAIN_SECONDS`）
→ 关监听 socket → 关审计连接与线程池 → 删临时文件 → 落「收尾完成」日志。
契约、异常恢复建议、如何新增服务：[`docs/服务编排说明.md`](docs/服务编排说明.md)。

## 4. 实测结果（全部为本机 2026-10-06 复跑所得）

| 指标 | 实测 | 口径 |
|---|---|---|
| 测试套件 | 全绿（382 条，150.9s） | `python -m pytest tests/ -q` |
| 变异测试 | 注入 12 / 捕获 **12/12** / 还原 MD5 一致 | `reports/变异测试.md` |
| 评测用例判分通过 | **166/170 = 97.6%**（T1 45/45、T2 45/45、T3 37/40、T4 39/40） | `reports/评测结果.md` |
| **错承诺条数（红线）** | **0** | 170 条输出中白名单外承诺 0 条。**不报拦截率类指标** |
| 意图 Top-1 | 154/170 = 90.6% | 规则分类器（非模型），高危档 77.5% |
| Hit@5 | 45/45 = 100% | 知识库仅 16 条目，**不外推到生产规模** |
| 工具调用执行成功率 | 267/267 = 100%（其中查到数据 259/267） | `not_found` 属正确行为，不算失败 |
| Correct handoff | 152/170 = 89.4%（漏转 8/75、多余转 10/95 分开列） | 目标 ≥98% 是**从业者模板口径，非行业标准** |
| 端到端延迟 | 服务内 p95 15ms（template）；HTTP 往返 p50 30ms / max 70ms（n=12，本机） | 模型链路**未测** |
| 服务起停 | 启动 0.6s 就绪；停机走「服务自机关闭」，端口释放与无孤儿校验通过 | `logs/api.log` 无脏退出（有断言把守） |
| 语料闸门 F-001 | **未通过**：8 个入口全部无法直取数据 | `reports/P0-语料闸门.md`（逐入口 HTTP 证据） |

每个数的「实测 / 设计值 / 未测」三态声明：**[`docs/口径声明.md`](docs/口径声明.md)**。
尚未完成的闭环（需要人来做的部分）：**[`docs/待补与歧义清单.md`](docs/待补与歧义清单.md)**，
其中 A5 的 kappa 已就绪但**必须由你独立标注 8 条样本后**才能出数：

```bash
python -m evals.kappa --selftest     # 校验计算器（与手算一致）
# 填 evals/human_sampling_5pct.csv 的 human_verdict 列后：
python -m evals.kappa
```

## 5. 架构（自研轻量编排，不引入 LangChain）

```
用户一句话（CLI 或 POST /v1/chat）
  → dialogue  槽位跟踪 / 指代消解 / 上下文预算
  → intents   规则关键词权重 → 3 一级 / 20 二级 + 高危集 + 诱导句式
  → tools     5 个确定性工具（Pydantic 入参校验、3s 超时、降级不给数字）→ SQLite
  → kb        双时态政策/FAQ（valid_from/valid_until + 版本链 + TTL + stale 监控，默认 BM25）
  → gates ★   只解释不确认 / 人工入口 / 承诺白名单（生成前剔除）/ 置信度 / fail-closed
  → llm       template（默认，确定性）| ollama | openai —— 只允许改写门禁批准的原子句
  → output_scan 第二道锁：承诺词典 + 数字溯源，违规整句替换
  → audit     幂等键 + 决策链 + 条文版本 + 工具证据 + 哈希链

编排层：ops/cli → 预检（端口/配置/可写）→ 按依赖启动 → 健康确认 → 优雅停机 + 残留回收
```

关键设计：**门禁在生成之前**。生成器的输入 `GenerationPlan` 里根本没有承诺类别，
所以「输出前拦截」是靠数据流形状实现的，不靠提示词自觉。
细节与接口契约：[`docs/架构图.md`](docs/架构图.md)。

## 6. 目录

```
src/          intents / tools / kb / dialogue / gates ★ / audit / llm / agent / cli / schemas / config
              app.py（应用容器）/ server.py（HTTP 服务 + 优雅停机）
ops/          process.py（跨平台进程控制）/ services.py（服务抽象与清单）/ manager.py / cli.py
data/         synthetic_shop/（schema.sql + seed.sql + kb_seed.sql）
              intents/（taxonomy.csv + jddc_label_map.csv）
              gates/（commitment_whitelist.csv + committal_lexicon.csv + urgency_lexicon.txt）
              jddc/（探测留证 + 论文存档 + 映射核对结果；不含语料本体）
evals/        golden_set_v1.jsonl（170 条四档）+ judge.py + metrics.py + run_eval.py + kappa.py
tests/        四条义务各一组 + 模块级 + 数据契约 + 口径纪律 + 交付物自检 + 编排真起真停（382 条）
scripts/      fetch_jddc.py / clean_jddc.py / verify_jddc_mapping.py / mutation_test.py / perf_probe.py
docs/         义务映射表 ★ / 意图体系 / 语料与许可留证 / 架构图 / 口径声明 / 演示脚本 / 待补清单
reports/      P0-语料闸门 / 评测结果 / 变异测试 / 延迟实测.json
```

## 7. 关键约束与异常处理（一句话版）

- **数值**：金额/库存/状态一律 SQL 产出，模型不参与计算；输出里出现无法溯源的数字即被拦（A9 实测通过）。
- **工具失败**：`INVALID_ARGS / TIMEOUT / ERROR / NOT_FOUND` 四种状态，降级时**不返回任何数字**，
  回答只能讲规则并转人工。
- **知识库失效**：条目过期或下线 → 检索剔除 + `stale_retrieval` 记录；高危问题无现行条文 → `fail_closed`，
  不编造规则。
- **模型不可用**：provider 报错 → 退回确定性模板渲染，`degraded=True` 落进审计。
- **服务停不干净**：优雅停机超时 → SIGTERM/taskkill → 仍不退 → 强杀进程树。端口未释放或有孤儿进程
  都会单独报失败并给出恢复命令；没验证过的事不会被写成「已停止」。
- **人工出口缺失**：先补齐（留 `G-009-post-check` 痕迹），补不齐直接 `HandoffMissing` 抛出，不发送。
- **重放**：同 `request_key` 不重判，返回原结论并 `replay_count += 1`。

## 8. 明确没做 / 做不到（未覆盖清单）

- 不做商城、下单支付、多商家平台、真实店铺 IM、峰值容量、真实拦截效果口径（方案 §1.3）。
- **JDDC 原始语料未入库**：官方发放页已失效、平台域名本机不可解析、境内镜像检索为空、
  历史对象存储桶不存在。清洗/抽样/映射核对管道已就绪并可复跑，等文件入库。
  详见 `reports/P0-语料闸门.md` 与 `docs/语料与许可留证.md`。
- 外部模型链路（延迟/成本/遵守度）、BGE-M3 向量检索、流式首包、真实流量指标：均未测，见 `docs/口径声明.md` §三。
- 国标里与**标识提示、个人信息处理、内容合规**相关的其他条款：本原型未实现，只覆盖四条资损/责任相关义务。
- 评测集是**单人自标**的 golden set，不是 JDDC 真值；kappa 只校准机判与人工判定的一致性，不校准 gold 本身。

## 9. 许可与数据声明

- 语料侧：本仓库**不附带、不镜像、不重分发** JDDC 原始数据；引用论文示例时标注出处（LREC 2020, pp.459–466）。
  论文页脚许可为 **CC-BY-NC**（非商业），数据集本身条款未取得——按「待核实 + 非商业引用」处理。
- 店铺侧：`data/synthetic_shop/` 全部为人工构造的合成记录，与任何真实商家、真实订单、真实快递单号无关。
- 代码许可：待定，见 `docs/待补与歧义清单.md` Q-16。

## 10. 简历口径（一句话，不含任何未验证宣称）

> 电商客服/导购 Agent 原型（JDDC 公开语料口径离线评测 + GB/T 47746 合规门禁，单人自主，无店铺、无真实流量）：
> 承诺白名单在生成前拦截、重大事项只解释不确认、人工入口每轮必达、决策链留痕可反查条文版本；
> 170 条四档离线评测错承诺 0 条，10 个注入缺陷全部被测试捕获。
