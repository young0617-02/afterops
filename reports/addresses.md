# 顾客地址簿

- 验证时间：2026-11-03
- 范围：地址簿 CRUD、默认地址、结算选择与预填
- 迁移：`3647aa3cea0c`（`customer_addresses`）

## 接口与页面

- `GET /api/store/addresses`：按默认、更新时间倒序列出本人地址。
- `POST /api/store/addresses`：新增；首个地址自动成为默认；完全重复的地址返回已有记录。
- `PATCH /api/store/addresses/{id}`：编辑；可同时设为默认。
- `DELETE /api/store/addresses/{id}`：删除；删除默认地址后自动把最近更新的剩余地址设为默认。
- `POST /api/store/addresses/{id}/default`：切换默认地址。
- `GET /api/store/addresses/last`：默认地址优先；没有地址簿时回退到最近一单的收货地址。
- 前端新增 `/addresses` 地址簿页与导航入口；结算页可从地址簿选择并自动填充表单。

## 规则

- 每个顾客最多 10 个地址。
- 手机号必须为 11 位大陆手机号；姓名、城市、详细地址不能为空白。
- 所有读写都按 `customer_id` 归属校验，他人地址返回 404。
- 地址修改按顾客行锁串行化，避免并发切换默认地址产生多个默认项。
- 结算订单保存地址快照，后续修改地址簿不影响历史订单。

## 验证结果

| 项 | 结果 |
|---|---|
| 后端测试 | `tests/test_addresses_api.py`：CRUD、首个默认、切换默认、删除默认后提升、越权 404、手机号/空白校验、10 个上限、重复地址、最近地址优先默认 |
| 迁移 | `alembic upgrade head` → `downgrade -1` → `upgrade head` 通过，当前 head `3647aa3cea0c` |
| 后端全量 | `uv run pytest -q`：**145 passed**（最终状态）；`ruff` 通过 |
| 前端 E2E | `npm run test:e2e`：**13 passed**；新增地址保存、刷新持久化、结算页选择后回填姓名与详细地址 |
| 任务评测 | `uv run python -m app.scripts.task_eval`：15/15 通过 |
| 审计链 | `uv run python -m app.scripts.verify_audit`：OK |

## 边界

- 地址不做短信/邮编/地理编码验证，也不接地图联想。
- 地址簿变更为顾客资料操作，未写审计链；订单快照仍在订单表中。
- 最多 10 个地址，无标签管理、批量导入、地址共享或企业地址。
- 结算页选择地址只填充表单；订单仍以提交时的地址快照为准。
