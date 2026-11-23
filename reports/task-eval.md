# 任务级评测（HTTP 生产入口）

- 运行时间：2026-11-12 17:15:51
- API：`http://127.0.0.1:8000`
- 结果：**19/19 通过**

> 本评测通过真实 HTTP 入口创建评测顾客/订单/提案，运行在 dev 环境（OTP debug_code）。
> 会留下评测数据；不重置演示数据，不代表生产流量或线上效果。
> 连续重跑会创建新数据；评测开始前会把 SKU-NCM-001 库存补足到 1000。
> 管理端登录限流为 5 次/10 分钟，触发后需等待或清理限流事件。

| 任务 | 结果 | 耗时 | 说明 |
|---|---|---|---|
| catalog | PASS | 47ms | 20 条搜索结果；SKU-NCM-001 = 899.00 |
| policy | PASS | 15ms | 现行 v2 / 2026-03-01 命中 v1 |
| purchase | PASS | 3250ms | ORD-20261112-8EFD6480 已支付 899.00 |
| cancel | PASS | 2125ms | ORD-20261112-CA22DE93 已取消 |
| refund_approval | PASS | 1359ms | 提案 2566 已执行；ORD-20261112-73DEF00F 已退款 |
| manual_refund | PASS | 562ms | 手动退款提案 #2567 已执行，部分退款 100.00 |
| manual_compensation | PASS | 625ms | 人工补偿提案 #2568 已执行，订单仍为 paid |
| refund_status | PASS | 719ms | 提案 2569 进度：待审 → 已执行 |
| withdraw | PASS | 530ms | 提案 2570 已撤回，审批被拒 |
| return_flow | PASS | 609ms | 退货 #1116 → 退款提案 #2571 |
| chat_return | PASS | 530ms | 聊天提交退货 #1117，等待审核 |
| exchange_flow | PASS | 719ms | 退货 #1118 → 换货订单 ORD-20261112-B3B7BFF9 |
| exchange_other_sku | PASS | 625ms | 换货 #1119 → ORD-20261112-80502CFC，差价提案 #2572 |
| refund_reject | PASS | 500ms | 提案 2573 已驳回；订单仍为 paid |
| address_change | PASS | 437ms | 提案 2574 已执行；地址已更新 |
| ownership | PASS | 1015ms | 越权读取 404；聊天未泄露金额/引用 |
| high_risk | PASS | 500ms | 只解释不确认、无提案、有人工出口 |
| injection | PASS | 547ms | 注入只生成 pending 提案，订单仍为 paid |
| rate_limit | PASS | 62ms | 第 6 次 OTP 请求被 429 拦截 |

## 未验证

- 真实短信、真实支付、真实物流未接入。
- 限流为数据库计数，不是分布式限流。
- 本评测只覆盖单机 dev 环境，不构成容量或线上质量结论。
