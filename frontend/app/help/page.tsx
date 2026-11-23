"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

import { api, errorMessage } from "@/lib/api";
import type { HelpCenter } from "@/lib/types";

const TOPIC_LABELS: Record<string, string> = {
  return: "退货",
  refund_time: "退款时效",
  procedure: "售后流程",
  compensation: "补偿",
  address_change: "改地址",
  warranty: "保修",
};

export default function HelpPage() {
  const [data, setData] = useState<HelpCenter | null>(null);
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    api<HelpCenter>("/api/store/help")
      .then((body) => {
        if (!cancelled) setData(body);
      })
      .catch((err) => {
        if (!cancelled) setError(errorMessage(err));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const policies = useMemo(() => {
    const keyword = query.trim().toLowerCase();
    if (!keyword) return data?.policies ?? [];
    return (data?.policies ?? []).filter((policy) =>
      [policy.title, policy.body, policy.topic, policy.entry_id]
        .join(" ")
        .toLowerCase()
        .includes(keyword),
    );
  }, [data, query]);

  return (
    <>
      <div className="section-head">
        <div>
          <h2>售后政策与帮助</h2>
          <p>
            以下为当前生效版本；AI
            客服回答会标注引用的政策版本，点引用可回到对应条文。
          </p>
        </div>
        <Link className="btn btn-ghost" href="/after-sales">
          售后进度
        </Link>
      </div>

      {error ? <div className="notice notice-error">{error}</div> : null}

      {data ? (
        <section className="panel panel-pad">
          <div className="section-head" style={{ marginBottom: 10 }}>
            <div>
              <h3 style={{ margin: 0 }}>退货规则摘要</h3>
              <p>结构化规则会同时用于顾客提交退货与 AI 回答。</p>
            </div>
          </div>
          <div className="overview-grid">
            <div className="overview-card">
              <span className="overview-label">签收后可退</span>
              <strong>{data.return_policy.window_days} 天</strong>
            </div>
            <div className="overview-card">
              <span className="overview-label">换货</span>
              <strong>
                {data.return_policy.allow_exchange ? "支持" : "不支持"}
              </strong>
            </div>
            <div className="overview-card">
              <span className="overview-label">退货运费</span>
              <strong>
                {data.return_policy.return_shipping_fee_cents > 0
                  ? `${(
                      data.return_policy.return_shipping_fee_cents / 100
                    ).toFixed(2)} 元`
                  : "无"}
              </strong>
            </div>
            <div className="overview-card">
              <span className="overview-label">禁退类目</span>
              <strong>
                {data.return_policy.final_sale_categories.length > 0
                  ? data.return_policy.final_sale_categories.join("、")
                  : "无"}
              </strong>
            </div>
            <div className="overview-card">
              <span className="overview-label">类目窗口</span>
              <strong>
                {data.return_policy.category_windows.length > 0
                  ? data.return_policy.category_windows
                      .map(
                        (item) =>
                          `${item.category} ${item.window_days} 天`,
                      )
                      .join("、")
                  : "未单独配置"}
              </strong>
            </div>
          </div>
        </section>
      ) : null}

      <div className="fulfillment-toolbar" style={{ marginTop: 16 }}>
        <input
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="搜索政策，如退货、退款到账"
          aria-label="搜索政策"
          data-testid="help-search"
        />
        <span className="muted">
          {loading ? "加载政策…" : `共 ${policies.length} 条现行政策`}
        </span>
      </div>

      {loading ? (
        <div className="loading">加载帮助中心…</div>
      ) : policies.length === 0 ? (
        <div className="empty-state">没有匹配的政策，换个关键词试试。</div>
      ) : (
        <div className="admin-grid">
          {policies.map((policy) => (
            <article
              className="proposal-card"
              id={`policy-${policy.entry_id}`}
              key={policy.entry_id}
              data-testid={`help-policy-${policy.entry_id}`}
            >
              <div className="proposal-head">
                <div className="proposal-title">
                  <strong>{policy.title}</strong>
                  <span className="badge badge-neutral">
                    {TOPIC_LABELS[policy.topic] ?? policy.topic}
                  </span>
                  <span className="badge badge-ok">{policy.version}</span>
                </div>
                <span className="muted">
                  {policy.valid_from} 至 {policy.valid_until ?? "长期"}
                </span>
              </div>
              <p className="after-sales-reason">{policy.body}</p>
              <div className="proposal-meta">
                <span>{policy.entry_id}</span>
                <span>来源：{policy.source}</span>
              </div>
            </article>
          ))}
        </div>
      )}
    </>
  );
}
