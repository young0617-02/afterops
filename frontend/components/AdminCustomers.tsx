"use client";

import { useCallback, useEffect, useState, type FormEvent } from "react";

import { StatusBadge } from "@/components/StatusBadge";
import { api, errorMessage } from "@/lib/api";
import {
  actionTypeLabel,
  formatDateTime,
  orderStatusLabel,
  paymentStatusLabel,
  proposalStatusLabel,
  returnStatusLabel,
} from "@/lib/status";
import type {
  AfterSalesTimelineEvent,
  CustomerDirectoryDetail,
  CustomerDirectoryRow,
} from "@/lib/types";

const TIMELINE_KIND_LABELS: Record<string, string> = {
  return: "退货",
  return_tracking: "退货物流",
  return_pickup: "上门取件",
  return_appeal: "扣减申诉",
  repair: "维修",
  repair_quote: "维修报价",
  proposal: "售后提案",
  refund: "退款",
  compensation: "补偿",
  invoice: "发票",
  handoff: "人工工单",
};

function timelineKindLabel(kind: string): string {
  return TIMELINE_KIND_LABELS[kind] ?? kind;
}

function riskLabel(level: string): string {
  if (level === "high") return "高风险";
  if (level === "medium") return "需关注";
  return "正常";
}

function riskTone(level: string): string {
  if (level === "high") return "badge-danger";
  if (level === "medium") return "badge-warn";
  return "badge-ok";
}

export function AdminCustomers() {
  const [queryInput, setQueryInput] = useState("");
  const [query, setQuery] = useState("");
  const [rows, setRows] = useState<CustomerDirectoryRow[]>([]);
  const [detail, setDetail] = useState<CustomerDirectoryDetail | null>(null);
  const [timeline, setTimeline] = useState<AfterSalesTimelineEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams({ limit: "50" });
      if (query.trim()) params.set("q", query.trim());
      setRows(await api<CustomerDirectoryRow[]>(
        `/api/admin/customers?${params.toString()}`,
      ));
      setError("");
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setLoading(false);
    }
  }, [query]);

  useEffect(() => {
    void load();
  }, [load]);

  const search = (event: FormEvent) => {
    event.preventDefault();
    setDetail(null);
    setQuery(queryInput.trim());
  };

  const openDetail = async (row: CustomerDirectoryRow) => {
    setBusyId(row.id);
    setError("");
    try {
      const [detailBody, timelineBody] = await Promise.all([
        api<CustomerDirectoryDetail>(`/api/admin/customers/${row.id}`),
        api<AfterSalesTimelineEvent[]>(
          `/api/admin/customers/${row.id}/timeline?limit=50`,
        ),
      ]);
      setDetail(detailBody);
      setTimeline(timelineBody);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusyId(null);
    }
  };

  return (
    <section className="admin-customers">
      <div className="fulfillment-toolbar">
        <form className="search-form" onSubmit={search}>
          <input
            value={queryInput}
            onChange={(event) => setQueryInput(event.target.value)}
            placeholder="手机号 / 姓名 / 邮箱"
            maxLength={80}
            aria-label="搜索客户"
            data-testid="customer-search"
          />
          <button className="btn btn-ghost btn-sm" type="submit">
            搜索
          </button>
          <button
            className="btn btn-ghost btn-sm"
            type="button"
            onClick={() => void load()}
          >
            刷新
          </button>
        </form>
        <span className="muted">客户档案只读；资金/地址动作仍走提案审批</span>
      </div>

      {error ? <div className="notice notice-error">{error}</div> : null}

      {loading ? (
        <div className="loading">加载客户…</div>
      ) : rows.length === 0 ? (
        <div className="empty-state">没有匹配的客户。</div>
      ) : (
        <div className="admin-grid">
          {rows.map((row) => (
            <article
              className="proposal-card"
              key={row.id}
              data-testid={`customer-${row.id}`}
            >
              <div className="proposal-head">
                <div className="proposal-title">
                  <strong>{row.name || "未命名顾客"}</strong>
                  {row.pending_proposals > 0 ? (
                    <StatusBadge
                      label={`待审批 ${row.pending_proposals}`}
                      tone="badge-warn"
                    />
                  ) : null}
                </div>
                <span className="muted">
                  {row.last_order_at
                    ? `最近下单 ${formatDateTime(row.last_order_at)}`
                    : "暂无订单"}
                </span>
              </div>
              <div className="proposal-meta">
                <span>{row.phone || "—"}</span>
                <span>{row.email || "未设置邮箱"}</span>
                <span>订单 {row.order_count}</span>
                <span>售后 {row.after_sales_count}</span>
                <span>进行中退货 {row.active_returns}</span>
              </div>
              <div className="proposal-actions">
                <button
                  className="btn btn-ghost btn-sm"
                  type="button"
                  disabled={busyId === row.id}
                  data-testid={`customer-detail-${row.id}`}
                  onClick={() => void openDetail(row)}
                >
                  {busyId === row.id ? "加载中" : "查看档案"}
                </button>
              </div>
            </article>
          ))}
        </div>
      )}

      {detail ? (
        <section
          className="panel panel-pad"
          style={{ marginTop: 16 }}
          data-testid="customer-detail"
        >
          <div className="section-head" style={{ marginBottom: 10 }}>
            <div>
              <h3 style={{ margin: 0 }}>
                {detail.name || "未命名顾客"} · {detail.phone || "—"}
              </h3>
              <p>
                {detail.email || "未设置邮箱"} · 订单 {detail.order_count} · 售后{" "}
                {detail.after_sales_count}
              </p>
            </div>
            <button
              className="btn btn-ghost btn-sm"
              type="button"
              onClick={() => {
                setDetail(null);
                setTimeline([]);
              }}
            >
              关闭档案
            </button>
          </div>

          <div
            className="audit-summary"
            data-testid="customer-risk"
          >
            <div className="handoff-counts">
              <StatusBadge
                label={`售后风险 ${riskLabel(
                  detail.after_sales_risk.level,
                )}`}
                tone={riskTone(detail.after_sales_risk.level)}
              />
              <span>
                退货 {detail.after_sales_risk.return_count} 次 / 订单{" "}
                {detail.after_sales_risk.order_count} 单
              </span>
              <span>
                退货率{" "}
                {Math.round(
                  detail.after_sales_risk.return_rate * 100,
                )}
                %
              </span>
              <span>
                放弃补差换货{" "}
                {detail.after_sales_risk.cancelled_exchange_count}
              </span>
              <span>
                扣减申诉 {detail.after_sales_risk.appeal_count}
              </span>
              <span>
                顾客责任{" "}
                {detail.after_sales_risk.customer_responsibility_count}
              </span>
            </div>
            <span className="muted">
              {detail.after_sales_risk.reasons.length > 0
                ? detail.after_sales_risk.reasons.join("；")
                : `近 ${detail.after_sales_risk.window_days} 天未发现售后风险信号`}
            </span>
          </div>

          <h4>订单</h4>
          {detail.orders.length === 0 ? (
            <div className="muted">暂无订单。</div>
          ) : (
            <div className="order-list">
              {detail.orders.map((order) => (
                <div
                  className="proposal-card"
                  key={order.order_no}
                  data-testid={`customer-order-${order.order_no}`}
                >
                  <div className="proposal-head">
                    <div className="proposal-title">
                      <strong>{order.order_no}</strong>
                      <StatusBadge
                        label={orderStatusLabel(order.status)}
                        status={order.status}
                      />
                    </div>
                    <span className="muted">
                      {formatDateTime(order.placed_at)}
                    </span>
                  </div>
                  <div className="proposal-meta">
                    <span>{order.payable_display} 元</span>
                    <span>支付 {paymentStatusLabel(order.payment_status)}</span>
                    <span>履约 {order.fulfillment_status}</span>
                  </div>
                </div>
              ))}
            </div>
          )}

          <h4>售后提案</h4>
          {detail.proposals.length === 0 ? (
            <div className="muted">暂无提案。</div>
          ) : (
            <div className="proposal-meta">
              {detail.proposals.map((proposal) => (
                <span key={proposal.id}>
                  #{proposal.id} {actionTypeLabel(proposal.action_type)} ·{" "}
                  {proposalStatusLabel(proposal.status)}
                  {proposal.amount_display
                    ? ` · ${proposal.amount_display} 元`
                    : ""}
                  {proposal.order_no ? ` · ${proposal.order_no}` : ""}
                </span>
              ))}
            </div>
          )}

          <h4>退货</h4>
          {detail.returns.length === 0 ? (
            <div className="muted">暂无退货。</div>
          ) : (
            <div className="proposal-meta">
              {detail.returns.map((request) => (
                <span key={request.id}>
                  退货 #{request.id} ·{" "}
                  {request.resolution === "exchange" ? "换货" : "退货退款"} ·{" "}
                  {returnStatusLabel(request.status)} · {request.order_no}
                </span>
              ))}
            </div>
          )}

          <h4>售后时间线</h4>
          {timeline.length === 0 ? (
            <div className="muted">暂无售后动态。</div>
          ) : (
            <ol
              className="after-sales-timeline"
              data-testid="admin-customer-timeline"
            >
              {timeline.map((event, index) => (
                <li
                  key={`${event.kind}-${event.entity_id}-${event.occurred_at}-${index}`}
                >
                  <div className="timeline-head">
                    <StatusBadge
                      label={timelineKindLabel(event.kind)}
                      tone="badge-neutral"
                    />
                    <strong>{event.title}</strong>
                    <time>{formatDateTime(event.occurred_at)}</time>
                  </div>
                  {event.detail ? (
                    <p className="timeline-detail">{event.detail}</p>
                  ) : null}
                  {event.order_no ? (
                    <span className="muted">订单 {event.order_no}</span>
                  ) : null}
                </li>
              ))}
            </ol>
          )}
        </section>
      ) : null}
    </section>
  );
}
