# 顾客帮助中心与政策引用

- 验证时间：2026-11-04
- 范围：公开现行政策、退货规则摘要、政策搜索、AI 引用直达条文
- 页面：`/help`；导航新增「帮助」

## 行为

1. `GET /api/store/help` 返回当前生效的全部政策正文（条目、主题、标题、正文、版本、来源、有效期）与结构化退货政策摘要（窗口、换货开关、禁退类目）。
2. 帮助页展示退货规则摘要与政策卡片，支持按标题/正文/主题/条目 ID 客户端搜索。
3. 政策卡片锚点为 `#policy-{entry_id}`，可直接从外部或聊天引用跳转。
4. 聊天回复中的政策引用（形如 `POLICY-RETURN@v2`）渲染为链接，点击进入 `/help#policy-POLICY-RETURN`。
5. 售后页退货区增加「查看售后政策」入口。
6. 帮助内容与 AI 检索共用 `list_current_policies`，政策发布新版本后无需额外同步。
7. 非政策引用（订单、商品等）保持纯文本，不生成链接。

## API

`GET /api/store/help`（公开）：

```json
{
  "policies": [
    {
      "entry_id": "POLICY-RETURN",
      "topic": "return",
      "title": "7 天无理由退货（现行）",
      "body": "...",
      "version": "v2",
      "source": "...",
      "valid_from": "2026-07-01",
      "valid_until": null,
      "published_at": "2026-06-20"
    }
  ],
  "return_policy": {
    "window_days": 7,
    "allow_exchange": true,
    "final_sale_categories": []
  }
}
```

## 验证结果

| 项 | 结果 |
|---|---|
| 后端测试 | `tests/test_help_api.py`：公开返回现行政策正文与退货摘要；修改退货政策并发布 v3 后帮助中心同步更新，旧版本不出现 |
| 后端全量 | `uv run pytest -q`：**185 passed**；`ruff check app tests` 通过 |
| 前端检查 | `npm run typecheck`、Docker `npm run build` 通过（新增 `/help` 路由） |
| 前端 E2E | `npm run test:e2e`：**24 passed**；搜索「退款到账」只剩退款时效政策；聊天引用 `POLICY-RETURN@v2` 点击跳到条文并可见 |
| 任务评测 | `uv run python -m app.scripts.task_eval`：19/19 通过 |
| 审计链 | `uv run python -m app.scripts.verify_audit`：merchant=5 OK |

## 边界

- 帮助中心只展示当前生效版本，不提供历史版本浏览；历史仍可在后台政策库查看。
- 搜索是前端子串匹配，没有分词、相关性排序或向量检索；政策数量大时需要服务端搜索。
- 内容只有政策条目，没有独立 FAQ、图文教程、视频或附件。
- 单商家演示：`GET /api/store/help` 使用演示商家解析；多商家公开站点需要按域名/租户解析。
- 聊天引用链接只识别 `条目@版本` 格式；其他引用类型不跳转。
- 没有帮助内容反馈、有用/无用投票、访问统计或搜索词分析。
- 不提供多语言、深色模式专项样式或无障碍朗读优化。
