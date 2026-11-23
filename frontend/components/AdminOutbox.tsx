"use client";

import { useCallback, useEffect, useState } from "react";

import { StatusBadge } from "@/components/StatusBadge";
import { api, errorMessage } from "@/lib/api";
import { formatDateTime, outboxStatusLabel } from "@/lib/status";
import type { OutboxEvent, OutboxList } from "@/lib/types";

const FILTERS = [
  { value: "failed", label: "失败" },
  { value: "pending", label: "待发送" },
  { value: "processing", label: "投递中" },
  { value: "processed", label: "已投递" },
  { value: "all", label: "全部" },
];

function outboxTone(status: string): string {
  if (status === "failed") return "badge-danger";
  if (status === "pending") return "badge-warn";
  if (status === "processed") return "badge-ok";
  return "badge-neutral";
}

export function AdminOutbox({ role }: { role: "owner" | "agent" }) {
  const [items, setItems] = useState<OutboxEvent[]>([]);
  const [counts, setCounts] = useState<Record<string, number>>({});
  const [status, setStatus] = useState("failed");
  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const body = await api<OutboxList>(
        `/api/admin/outbox?status=${status}&limit=100`,
      );
      setItems(body.items);
      setCounts(body.counts);
      setError("");
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setLoading(false);
    }
  }, [status]);

  useEffect(() => {
    void load();
  }, [load]);

  const retry = async (item: OutboxEvent) => {
    setBusyId(item.id);
    setError("");
    setNotice("");
    try {
      await api(`/api/admin/outbox/${item.id}/retry`, { method: "POST" });
      setNotice(`投递事件 #${item.id} 已重新排队`);
      await load();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusyId(null);
    }
  };

  return (
    <section className="admin-outbox">
      <div className="audit-summary">
        <div className="handoff-counts">
          <span>失败 {counts.failed ?? 0}</span>
          <span>待发送 {counts.pending ?? 0}</span>
          <span>投递中 {counts.processing ?? 0}</span>
          <span>已投递 {counts.processed ?? 0}</span>
        </div>
        <span className="muted">
          失败事件可重新排队；实际发送由 worker 完成
        </span>
      </div>

      <div className="fulfillment-toolbar">
        <select
          value={status}
          onChange={(event) => setStatus(event.target.value)}
          aria-label="按投递状态筛选"
          data-testid="outbox-filter"
        >
          {FILTERS.map((item) => (
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

      {role !== "owner" ? (
        <div className="notice notice-warn">
          客服账号只能查看投递状态；重试需要店主权限。
        </div>
      ) : null}
      {notice ? <div className="notice notice-success">{notice}</div> : null}
      {error ? <div className="notice notice-error">{error}</div> : null}

      {loading ? (
        <div className="loading">加载投递事件…</div>
      ) : items.length === 0 ? (
        <div className="empty-state">当前筛选下没有投递事件。</div>
      ) : (
        <div className="admin-grid">
          {items.map((item) => (
            <article
              className="proposal-card"
              key={item.id}
              data-testid={`outbox-${item.id}`}
            >
              <div className="proposal-head">
                <div className="proposal-title">
                  <strong>#{item.id} {item.subject || item.event_type}</strong>
                  <StatusBadge
                    label={outboxStatusLabel(item.status)}
                    tone={outboxTone(item.status)}
                  />
                </div>
                <span className="muted">
                  {formatDateTime(item.created_at)}
                </span>
              </div>
              <div className="proposal-meta">
                <span>收件人 {item.to || "—"}</span>
                <span>尝试 {item.attempts}</span>
                {item.processed_at ? (
                  <span>完成 {formatDateTime(item.processed_at)}</span>
                ) : null}
              </div>
              {item.last_error ? (
                <p className="after-sales-reason">
                  最近错误：{item.last_error}
                </p>
              ) : null}
              {role === "owner" &&
              (item.status === "failed" || item.status === "pending") ? (
                <div className="proposal-actions">
                  <button
                    className="btn btn-primary btn-sm"
                    type="button"
                    data-testid={`retry-outbox-${item.id}`}
                    disabled={busyId === item.id}
                    onClick={() => void retry(item)}
                  >
                    {busyId === item.id ? "排队中" : "重新排队"}
                  </button>
                </div>
              ) : null}
            </article>
          ))}
        </div>
      )}
    </section>
  );
}
