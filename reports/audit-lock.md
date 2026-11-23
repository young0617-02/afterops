# 审计链并发追加死锁修复

- 验证时间：2026-11-04
- 触发：任务评测与前端 E2E 并行运行时，`POST /api/admin/action-proposals` 返回 500
- 范围：`backend/app/services/audit.py` 的按商家审计链串行化

## 问题

`append_event` 用 `SELECT merchants.id ... FOR UPDATE` 锁商户行来串行化审计链。真实请求在追加审计前通常已插入子表行（订单、提案、聊天消息等），这些插入会对外键父行持有 `KEY SHARE` 锁；两个并发事务各持 `KEY SHARE`、再同时申请 `FOR UPDATE`，形成锁环：

```
Process A waits for ShareLock on transaction B; blocked by B
Process B waits for ShareLock on transaction A; blocked by A
CONTEXT: while locking tuple in relation "merchants"
```

PostgreSQL 检测到死锁后中止其中一个事务，接口返回 500。任务评测中的手动退款提案因此失败。

## 修复

- 改用事务级 advisory lock：`pg_advisory_xact_lock(0x41465452, merchant_id)`，按商家串行化哈希链追加。
- 两键命名空间与结算/支付使用的单键 `pg_advisory_xact_lock(key)` 隔离，避免无关串行化。
- advisory lock 不参与外键行锁的等待关系，不会与 `KEY SHARE` 形成锁环。
- 非 PostgreSQL（SQLite 测试）保持无锁路径，行为不变。
- 链尾读取、`prev_hash` 计算与写入仍在同一事务内，提交时释放 advisory lock。

## 验证结果

| 项 | 结果 |
|---|---|
| 并发探针（修复后） | `uv run python -m app.scripts.audit_deadlock_probe`：**OK 2 concurrent appends, chain intact, events=2** |
| 反面对照（临时还原行锁） | 同一探针：**FAIL**，`DeadlockDetected ... FOR UPDATE`，证明探针能捕获该缺陷 |
| 后端全量 | `uv run pytest -q`：**167 passed**；`ruff check app tests` 通过 |
| 任务评测 | `uv run python -m app.scripts.task_eval`：**19/19 通过**（含此前失败的 `manual_refund`） |
| 审计链 | `uv run python -m app.scripts.verify_audit`：merchant=5 OK |
| 前端 E2E | `npm run test:e2e`：**20 passed** |

探针流程：创建临时商家 → 两个线程各自插入顾客/通知（取得外键 `KEY SHARE`）→ 栅栏同步 → 并发追加审计事件 → 校验链完整且事件数为 2 → 删除临时商家（级联清理）。探针不污染演示商家的审计链。

## 边界

- advisory lock 只在同一 PostgreSQL 实例内生效；多实例集群共享同一数据库时仍有效，但连接池/事务边界必须保持当前用法。
- 审计链完整性仍要求所有审计写入都经过 `append_event`；绕过服务直接插表的写入不会被串行化。
- 修复只消除这一处锁环；其他业务锁（订单、库存、提案）仍按各自顺序加锁，未做全局死锁重试。
- 链校验仍是全表顺序扫描，事件量大时需要增量校验/分段校验。
- 探针为 2 并发确定性复现，不是容量压测；高并发下的审计写入吞吐未测量。
