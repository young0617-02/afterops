"use client";

import { useEffect, useState } from "react";

import { api, errorMessage } from "@/lib/api";
import { formatDateTime } from "@/lib/status";
import type { ReturnAnalytics } from "@/lib/types";

function formatCents(value: number): string {
  return (value / 100).toFixed(2);
}

export function AdminReturnAnalytics() {
  const [days, setDays] = useState(30);
  const [data, setData] = useState<ReturnAnalytics | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    api<ReturnAnalytics>(`/api/admin/returns/analytics?days=${days}`)
      .then((body) => {
        if (!cancelled) {
          setData(body);
          setError("");
        }
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
  }, [days]);

  return (
    <details
      className="panel panel-pad return-analytics"
      data-testid="return-analytics"
    >
      <summary>售后分析（退货原因 / 状态 / 判责 / Top SKU）</summary>
      <div className="fulfillment-toolbar" style={{ marginTop: 12 }}>
        <select
          value={days}
          onChange={(event) => setDays(Number(event.target.value))}
          aria-label="售后分析时间窗口"
          data-testid="return-analytics-days"
        >
          <option value={7}>近 7 天</option>
          <option value={30}>近 30 天</option>
          <option value={90}>近 90 天</option>
        </select>
        <a
          className="btn btn-ghost btn-sm"
          href={`/api/admin/returns/inspection-report.csv?days=${days}`}
          data-testid="return-inspection-report"
        >
          导出质检报告 CSV
        </a>
        {data ? (
          <span className="muted">
            统计起点 {formatDateTime(data.since)}
          </span>
        ) : null}
      </div>
      {error ? <div className="notice notice-error">{error}</div> : null}
      {loading && !data ? (
        <div className="loading">加载售后分析…</div>
      ) : data ? (
        <>
          <div className="overview-grid">
            <div className="overview-card">
              <span className="overview-label">退货申请</span>
              <strong>{data.total}</strong>
            </div>
            <div className="overview-card">
              <span className="overview-label">待审核</span>
              <strong>{data.status_counts.requested ?? 0}</strong>
            </div>
            <div className="overview-card">
              <span className="overview-label">已批准/回寄</span>
              <strong>
                {(data.status_counts.approved ?? 0) +
                  (data.status_counts.in_transit ?? 0)}
              </strong>
            </div>
            <div className="overview-card">
              <span className="overview-label">已收货</span>
              <strong>{data.status_counts.received ?? 0}</strong>
            </div>
            <div className="overview-card">
              <span className="overview-label">驳回/撤回</span>
              <strong>
                {(data.status_counts.rejected ?? 0) +
                  (data.status_counts.withdrawn ?? 0)}
              </strong>
            </div>
            <div className="overview-card">
              <span className="overview-label">退款金额</span>
              <strong>{formatCents(data.refund_total_cents)} 元</strong>
            </div>
            <div className="overview-card">
              <span className="overview-label">批准率</span>
              <strong>{(data.approval_rate * 100).toFixed(1)}%</strong>
            </div>
            <div className="overview-card">
              <span className="overview-label">平均审批</span>
              <strong>
                {data.avg_approval_hours === null
                  ? "—"
                  : `${data.avg_approval_hours} 小时`}
              </strong>
            </div>
            <div className="overview-card">
              <span className="overview-label">回补件数</span>
              <strong>{data.restocked_units}</strong>
            </div>
            <div className="overview-card">
              <span className="overview-label">报损件数</span>
              <strong data-testid="return-analytics-damaged-units">
                {data.damaged_units}
              </strong>
            </div>
            <div className="overview-card">
              <span className="overview-label">报损成本</span>
              <strong data-testid="return-analytics-damaged-cost">
                {formatCents(data.damaged_cost_cents)} 元
              </strong>
            </div>
          </div>

          <div className="admin-grid" style={{ marginTop: 12 }}>
            {data.reasons.map((item) => (
              <article
                className="proposal-card"
                key={item.category}
                data-testid={`return-analytics-reason-${item.category}`}
              >
                <div className="proposal-head">
                  <strong>{item.label}</strong>
                  <span className="badge badge-neutral">{item.count}</span>
                </div>
              </article>
            ))}
          </div>

          <h4 style={{ marginTop: 14 }}>责任判定</h4>
          <div className="admin-grid">
            {data.responsibilities.map((item) => (
              <article
                className="proposal-card"
                key={item.responsibility}
                data-testid={`return-analytics-responsibility-${item.responsibility}`}
              >
                <div className="proposal-head">
                  <strong>{item.label}</strong>
                  <span className="badge badge-neutral">
                    {item.items} 项 / {item.qty} 件
                  </span>
                </div>
                <p className="muted">
                  扣减 {formatCents(item.deduction_cents)} 元
                </p>
              </article>
            ))}
          </div>

          <h4 style={{ marginTop: 14 }}>Top 退货商品</h4>
          {data.top_skus.length === 0 ? (
            <div className="muted">窗口内没有退货商品。</div>
          ) : (
            <table className="table" data-testid="return-analytics-top-skus">
              <thead>
                <tr>
                  <th>商品</th>
                  <th>SKU</th>
                  <th>申请数</th>
                  <th>件数</th>
                </tr>
              </thead>
              <tbody>
                {data.top_skus.map((row) => (
                  <tr key={row.sku}>
                    <td>{row.title}</td>
                    <td>{row.sku}</td>
                    <td>{row.requests}</td>
                    <td>{row.qty}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </>
      ) : null}
    </details>
  );
}