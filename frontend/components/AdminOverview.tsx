"use client";

import { useCallback, useEffect, useState } from "react";

import { StatusBadge } from "@/components/StatusBadge";
import { api, errorMessage } from "@/lib/api";
import { formatDateOnly, formatDateTime } from "@/lib/status";
import type { Overview, TrendDay } from "@/lib/types";

type AdminTab =
  | "proposals"
  | "handoffs"
  | "returns"
  | "fulfillment"
  | "audit";

const RANGES = [
  { value: "24", label: "近 24 小时", trendDays: 7 },
  { value: "168", label: "近 7 天", trendDays: 7 },
  { value: "720", label: "近 30 天", trendDays: 30 },
];

function medianLabel(seconds: number | null): string {
  if (seconds === null) return "暂无审批";
  if (seconds < 60) return `${seconds} 秒`;
  return `${(seconds / 60).toFixed(1)} 分钟`;
}

export function AdminOverview({
  onNavigate,
}: {
  onNavigate: (tab: AdminTab) => void;
}) {
  const [overview, setOverview] = useState<Overview | null>(null);
  const [trend, setTrend] = useState<TrendDay[]>([]);
  const [range, setRange] = useState("24");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const trendDays = range === "720" ? 30 : 7;
  const rangeLabel =
    RANGES.find((item) => item.value === range)?.label ?? "近 24 小时";

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [overviewBody, trendBody] = await Promise.all([
        api<Overview>(`/api/admin/overview?hours=${range}`),
        api<TrendDay[]>(`/api/admin/overview/trend?days=${trendDays}`),
      ]);
      setOverview(overviewBody);
      setTrend(trendBody);
      setError("");
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setLoading(false);
    }
  }, [range, trendDays]);

  const exportTrend = () => {
    const header = "日期,支付笔数,支付金额,退款笔数,退款金额,AI回复,提案,工单";
    const rows = trend.map((day) =>
      [
        day.date,
        day.paid_orders,
        day.paid_amount_display,
        day.refunds,
        day.refund_amount_display,
        day.ai_chats,
        day.proposals,
        day.handoffs,
      ].join(","),
    );
    const blob = new Blob([`\ufeff${[header, ...rows].join("\n")}`], {
      type: "text/csv;charset=utf-8",
    });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `afterops-trend-${range}h.csv`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
  };

  useEffect(() => {
    void load();
  }, [load]);

  if (loading) return <div className="loading">加载运营概览…</div>;
  if (error) return <div className="notice notice-error">{error}</div>;
  if (!overview) return null;

  return (
    <section className="admin-overview">
      <div className="fulfillment-toolbar">
        <span className="muted">
          数据截至 {formatDateTime(overview.generated_at)} · 统计窗口{" "}
          {overview.window_hours} 小时
        </span>
        <select
          value={range}
          onChange={(event) => setRange(event.target.value)}
          aria-label="统计时间范围"
          data-testid="overview-range"
        >
          {RANGES.map((item) => (
            <option key={item.value} value={item.value}>
              {item.label}
            </option>
          ))}
        </select>
        <button
          className="btn btn-ghost btn-sm"
          type="button"
          onClick={() => void load()}
        >
          刷新
        </button>
      </div>

      <div className="overview-section">
        <h3>待办</h3>
        <div className="overview-grid">
          <button
            className="overview-card todo"
            type="button"
            data-testid="overview-pending-proposals"
            onClick={() => onNavigate("proposals")}
          >
            <span className="overview-label">待审批提案</span>
            <strong>{overview.pending_proposals}</strong>
            <small>资金/地址动作等待店主决策</small>
          </button>
          <button
            className="overview-card todo"
            type="button"
            data-testid="overview-pending-handoffs"
            onClick={() => onNavigate("handoffs")}
          >
            <span className="overview-label">待接管工单</span>
            <strong>{overview.pending_handoffs}</strong>
            <small>
              处理中 {overview.claimed_handoffs} · 紧急{" "}
              {overview.urgent_handoffs}
            </small>
          </button>
          <button
            className="overview-card todo"
            type="button"
            data-testid="overview-unfulfilled"
            onClick={() => onNavigate("fulfillment")}
          >
            <span className="overview-label">待履约订单</span>
            <strong>{overview.unfulfilled_orders}</strong>
            <small>已支付、未进入运输</small>
          </button>
          <button
            className="overview-card todo"
            type="button"
            data-testid="overview-pending-returns"
            onClick={() => onNavigate("returns")}
          >
            <span className="overview-label">待处理退货</span>
            <strong>{overview.pending_returns}</strong>
            <small>待审核/待回寄/已寄回</small>
          </button>
          <div className="overview-card">
            <span className="overview-label">未读通知</span>
            <strong>{overview.unread_notifications}</strong>
            <small>当前账号的站内通知</small>
          </div>
        </div>
      </div>

      <div className="overview-section">
        <h3>{rangeLabel}</h3>
        <div className="overview-grid">
          <div className="overview-card" data-testid="overview-paid">
            <span className="overview-label">支付</span>
            <strong>{overview.paid_orders} 笔</strong>
            <small>{overview.paid_amount_display} 元 · 含后续退款</small>
          </div>
          <div className="overview-card">
            <span className="overview-label">退款</span>
            <strong>{overview.refunds} 笔</strong>
            <small>{overview.refund_amount_display} 元</small>
          </div>
          <div className="overview-card">
            <span className="overview-label">AI 回复</span>
            <strong>{overview.ai_chats}</strong>
            <small>带审计的对话回复</small>
          </div>
          <div className="overview-card">
            <span className="overview-label">新售后提案</span>
            <strong>{overview.proposals_created}</strong>
            <small>
              执行 {overview.proposals_executed} · 驳回{" "}
              {overview.proposals_rejected}
            </small>
          </div>
          <div className="overview-card">
            <span className="overview-label">新人工工单</span>
            <strong>{overview.handoffs_created}</strong>
            <small>解决 {overview.handoffs_resolved}</small>
          </div>
          <div className="overview-card">
            <span className="overview-label">审批中位耗时</span>
            <strong>{medianLabel(overview.approval_median_seconds)}</strong>
            <small>创建到批准/驳回</small>
          </div>
        </div>
      </div>

      <div className="overview-section">
        <div className="section-head" style={{ marginBottom: 8 }}>
          <h3>近 {trendDays} 天趋势</h3>
          <button
            className="btn btn-ghost btn-sm"
            type="button"
            data-testid="export-trend"
            onClick={exportTrend}
          >
            导出 CSV
          </button>
        </div>
        <div className="trend-table">
          <div className="trend-row trend-head">
            <span>日期</span>
            <span>支付</span>
            <span>退款</span>
            <span>AI 回复</span>
            <span>提案</span>
            <span>工单</span>
          </div>
          {trend.map((day) => (
            <div className="trend-row" key={day.date}>
              <span>{formatDateOnly(day.date)}</span>
              <span>
                {day.paid_orders} 笔
                <small>{day.paid_amount_display} 元</small>
              </span>
              <span>
                {day.refunds} 笔
                <small>{day.refund_amount_display} 元</small>
              </span>
              <span>{day.ai_chats}</span>
              <span>{day.proposals}</span>
              <span>{day.handoffs}</span>
            </div>
          ))}
        </div>
      </div>

      <div className="overview-audit" data-testid="overview-audit-status">
        <StatusBadge
          label={overview.audit_valid ? "哈希链完整" : "哈希链断裂"}
          tone={overview.audit_valid ? "badge-ok" : "badge-danger"}
        />
        <span className="muted">
          已校验 {overview.audit_checked_events} 条审计事件
          {overview.audit_broken_at
            ? ` · 首个异常事件 #${overview.audit_broken_at}`
            : ""}
        </span>
        <button
          className="btn btn-ghost btn-sm"
          type="button"
          onClick={() => onNavigate("audit")}
        >
          查看审计链
        </button>
      </div>
    </section>
  );
}
