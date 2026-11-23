# 放弃补差换货与退款提案

- 验证时间：2026-11-10
- 范围：补差价待支付时，顾客或店主可取消换货；关闭替换订单、释放被占用库存，并按退货规则生成原订单退款提案（仍需店主审批）
- 迁移：`a1c3e5f7b9d2_exchange_cancellation.py`，新增换货取消时间/原因/操作方字段，退货状态加入 `exchange_cancelled`，补差支付状态加入 `cancelled`

## 行为

1. 顾客在售后页对“待支付补差价”的换货可点击「放弃换货并申请退款」；店主在后台退货页可点击「取消换货并生成退款提案」。客服账号只读。
2. 仅当退货已收货、补差待支付、替换订单未履约且未创建运单时允许取消；已支付补差、已发货、已取消或非换货申请均被拒绝。
3. 取消时释放替换订单占用的库存：逐条把 `committed` 预留置为 `released`，回补同仓可售数量并写 `exchange_cancelled_restock` 库存流水。
4. 替换订单置为 `cancelled`，零元支付状态置为 `refunded`；之后创建运单会被订单状态拦截。
5. 退款金额按现有退货口径计算：全额或部分退货的商品金额减去订单折扣分摊与退货运费；换货收货本身禁止质检扣减，因此不再额外扣减。
6. 退款金额大于 0 时自动生成 `system` 发起的退款提案并回填到退货记录；店主审批后才实际退款。金额为 0 时只记录取消与原因。
7. 退货状态置为 `exchange_cancelled`，补差支付状态置为 `cancelled`，记录取消时间、原因与操作方（customer/staff）。
8. 顾客放弃时通知全体员工；无论谁操作都会通知顾客。写 `exchange_cancelled` 审计，包含替换订单号、补差金额、退款提案与逐仓回补明细。
9. 售后时间线新增「换货已取消」事件，正文含取消原因、操作方与退款提案/金额。
10. 后台退货列表新增「换货已取消」筛选与计数；顾客与后台均展示取消原因和退款提案号。

## 验证结果

| 项 | 结果 |
|---|---|
| 后端测试 | `tests/test_exchange_cancellation.py`：顾客取消释放库存并生成 89900 元提案、二次取消/取消后支付 409、退款批准后订单变已退款、审计与通知/时间线；已支付补差不可取消；其他顾客 404；店主取消成功并通知顾客 |
| 后端全量 | `.venv/Scripts/python.exe -m pytest -q`：**333 passed**；`.venv/Scripts/ruff check app tests` 通过 |
| 前端检查 | `npm run typecheck` 通过；web 容器重建构建通过 |
| 前端 E2E | 新增「customer abandons exchange supplement and owner refunds」：购买→发货→高价换货→收货待补差→顾客取消→库存释放→后台筛选→店主批准退款→订单已退款；全量 **43 passed** |
| 迁移 | 临时 Postgres 库从空库 `upgrade head`（全链）→ `downgrade -1` → `upgrade head` 均成功，head = `a1c3e5f7b9d2`；dev 库已应用同一迁移 |
| 实机数据 | dev 库现有 4 条 `exchange_cancelled`（补差 6000、退款 4000）、4 条 `exchange_cancelled` 审计、4 条 `exchange_cancelled_restock` 流水；对应替换订单均为 cancelled/refunded |
| 任务评测 | `NO_PROXY= python -m app.scripts.task_eval`：19/19 通过 |
| 审计链 | `python -m app.scripts.verify_audit`：merchant=5 OK |
| 容器 | 10 个服务 Up，api/postgres/mailpit healthy；`/health` 200、web 200 |

## 边界

- 只在补差待支付阶段可取消；已支付、已发货、已完成的换货不能走此流程，也没有补差超时自动取消或催付。
- 取消后默认按退货规则生成退款提案，不提供“退回原商品”“部分明细取消换货”或改换其他 SKU 的选项；退款仍需店主审批。
- 替换订单为零元订单，取消后支付状态标为 refunded 但不会产生退款流水；原订单退款才进入退款账本与渠道结算。
- 操作原因只接受可选文本（默认“顾客放弃补差换货/商家取消补差换货”），无原因枚举、审批流或二次确认记录。
- 取消接口非幂等：重复调用返回 409，不会重复生成提案或重复回补库存。
- 迁移的 downgrade 会恢复旧状态约束；若库中已存在 `exchange_cancelled`/`cancelled` 数据，降级会因约束失败而中止，需先人工处理数据，属有意的不静默丢状态设计。
- 未接入 AI 自动建议放弃换货、风控拦截或顾客短信/IM 通知。
