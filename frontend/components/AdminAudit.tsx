"use client";

import { useCallback, useEffect, useState } from "react";

import { StatusBadge } from "@/components/StatusBadge";
import { api, errorMessage } from "@/lib/api";
import { auditEventTypeLabel, formatDateTime } from "@/lib/status";
import type {
  AuditAnchorStatus,
  AuditEvent,
  AuditList,
  AuditVerify,
} from "@/lib/types";

const HIGHLIGHT_KEYS: { key: string; label: string }[] = [
  { key: "order_no", label: "订单" },
  { key: "intent", label: "意图" },
  { key: "action_type", label: "动作" },
  { key: "status", label: "状态" },
  { key: "amount_cents", label: "金额" },
  { key: "carrier", label: "承运商" },
  { key: "tracking_no", label: "运单" },
  { key: "reason", label: "原因" },
];

function highlightValue(key: string, value: unknown): string | null {
  if (value === null || value === undefined || value === "") return null;
  if (key === "amount_cents" && typeof value === "number") {
    return `${(value / 100).toFixed(2)} 元`;
  }
  if (typeof value === "object") return null;
  return String(value);
}

function EventHighlights({ event }: { event: AuditEvent }) {
  const parts = HIGHLIGHT_KEYS.flatMap((item) => {
    const value = highlightValue(item.key, event.payload[item.key]);
    return value ? [`${item.label} ${value}`] : [];
  });
  if (!parts.length) return null;
  return (
    <div className="proposal-meta">
      {parts.slice(0, 5).map((part, index) => (
        <span key={`${part}-${index}`}>{part}</span>
      ))}
    </div>
  );
}

export function AdminAudit() {
  const [items, setItems] = useState<AuditEvent[]>([]);
  const [eventTypes, setEventTypes] = useState<string[]>([]);
  const [verification, setVerification] = useState<AuditVerify | null>(null);
  const [anchors, setAnchors] = useState<AuditAnchorStatus | null>(null);
  const [filter, setFilter] = useState("");
  const [searchInput, setSearchInput] = useState("");
  const [query, setQuery] = useState("");
  const [total, setTotal] = useState(0);
  const [hasMore, setHasMore] = useState(false);
  const [nextBefore, setNextBefore] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const querySuffix = useCallback(() => {
    const params = new URLSearchParams();
    if (filter) params.set("event_type", filter);
    if (query.trim()) params.set("q", query.trim());
    return params.toString() ? `&${params.toString()}` : "";
  }, [filter, query]);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [list, verify, anchorStatus] = await Promise.all([
        api<AuditList>(`/api/admin/audit?limit=50${querySuffix()}`),
        api<AuditVerify>("/api/admin/audit/verify"),
        api<AuditAnchorStatus>("/api/admin/audit/anchors"),
      ]);
      setItems(list.items);
      setEventTypes(list.event_types);
      setTotal(list.total);
      setHasMore(list.has_more);
      setNextBefore(list.next_before_id);
      setVerification(verify);
      setAnchors(anchorStatus);
      setError("");
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setLoading(false);
    }
  }, [filter, query, querySuffix]);

  useEffect(() => {
    void load();
  }, [load]);

  const loadMore = async () => {
    if (!nextBefore) return;
    setLoadingMore(true);
    try {
      const list = await api<AuditList>(
        `/api/admin/audit?limit=50&before_id=${nextBefore}${querySuffix()}`,
      );
      setItems((previous) => [...previous, ...list.items]);
      setHasMore(list.has_more);
      setNextBefore(list.next_before_id);
      setTotal(list.total);
      setError("");
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setLoadingMore(false);
    }
  };

  const exportAudit = async () => {
    setError("");
    setNotice("");
    try {
      const response = await fetch(
        `/api/admin/audit/export?limit=1000${querySuffix()}`,
        { credentials: "include" },
      );
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = "afterops-audit.json";
      anchor.click();
      URL.revokeObjectURL(url);
      setNotice("审计 JSON 已开始下载");
    } catch (err) {
      setError(errorMessage(err));
    }
  };

  return (
    <section className="admin-audit">
      <div className="audit-summary">
        <div className="audit-verify">
          {verification ? (
            <StatusBadge
              label={verification.valid ? "哈希链完整" : "哈希链断裂"}
              tone={verification.valid ? "badge-ok" : "badge-danger"}
            />
          ) : (
            <span className="muted">校验中…</span>
          )}
          <span className="muted">链上共校验 {verification?.checked_events ?? 0} 条事件</span>
          {verification && !verification.valid ? (
            <strong className="audit-broken">
              首个异常事件 #{verification.broken_at}
            </strong>
          ) : null}
        </div>
        <span className="muted">当前筛选 {total} 条</span>
      </div>

      <div
        className="audit-summary"
        data-testid="audit-anchors"
        style={{ marginTop: 8 }}
      >
        <div className="audit-verify">
          <StatusBadge
            label={
              !anchors
                ? "锚点校验中"
                : !anchors.configured
                  ? "未配置外部锚点"
                  : anchors.chain_valid && anchors.head_covered
                    ? "锚点覆盖链头"
                    : anchors.chain_valid
                      ? "锚点链完整，但未覆盖最新事件"
                      : "锚点校验失败"
            }
            tone={
              !anchors || !anchors.configured
                ? "badge-neutral"
                : anchors.chain_valid && anchors.head_covered
                  ? "badge-ok"
                  : anchors.chain_valid
                    ? "badge-warn"
                    : "badge-danger"
            }
          />
          <span className="muted">
            {anchors
              ? `锚点 ${anchors.count} 个 · 已锚定事件 #${anchors.anchored_through_id ?? "—"} · 当前链头 #${anchors.current_head_id ?? "—"}`
              : "读取锚点中…"}
          </span>
          {anchors && anchors.file_errors > 0 ? (
            <strong className="audit-broken">
              锚点文件有 {anchors.file_errors} 行无法解析
            </strong>
          ) : null}
        </div>
        <span className="muted">HMAC 密钥在数据库外，锚点文件独立卷存储</span>
      </div>

      {anchors && anchors.anchors.length > 0 ? (
        <div className="proposal-meta" style={{ marginTop: 6 }}>
          {anchors.anchors.slice(-5).reverse().map((anchor) => (
            <span key={`${anchor.audit_id}-${anchor.signature}`}>
              #{anchor.audit_id} · {formatDateTime(anchor.anchored_at)} ·{" "}
              {anchor.valid ? "有效" : "异常"}
            </span>
          ))}
        </div>
      ) : null}

      <div className="fulfillment-toolbar">
        <form
          className="audit-search"
          onSubmit={(event) => {
            event.preventDefault();
            setQuery(searchInput);
          }}
        >
          <input
            value={searchInput}
            onChange={(event) => setSearchInput(event.target.value)}
            placeholder="搜索订单号、运单号、提案号…"
            maxLength={120}
            aria-label="搜索审计事件"
            data-testid="audit-search"
          />
          <button className="btn btn-ghost btn-sm" type="submit">
            搜索
          </button>
          {query ? (
            <button
              className="btn btn-ghost btn-sm"
              type="button"
              onClick={() => {
                setSearchInput("");
                setQuery("");
              }}
            >
              清除
            </button>
          ) : null}
        </form>
        <div className="card-actions">
          <select
            value={filter}
            onChange={(event) => setFilter(event.target.value)}
            aria-label="按事件类型筛选"
            data-testid="audit-filter"
          >
            <option value="">全部事件</option>
            {eventTypes.map((type) => (
              <option key={type} value={type}>
                {auditEventTypeLabel(type)}
              </option>
            ))}
          </select>
          <button
            className="btn btn-ghost btn-sm"
            type="button"
            data-testid="audit-export"
            onClick={() => void exportAudit()}
          >
            导出 JSON
          </button>
          <button
            className="btn btn-ghost btn-sm"
            type="button"
            onClick={() => void load()}
          >
            重新校验
          </button>
        </div>
      </div>

      {notice ? <div className="notice notice-success">{notice}</div> : null}
      {error ? <div className="notice notice-error">{error}</div> : null}

      {loading ? (
        <div className="loading">加载审计事件…</div>
      ) : items.length === 0 ? (
        <div className="empty-state">当前筛选下没有审计事件。</div>
      ) : (
        <>
          <div className="admin-grid">
            {items.map((event) => (
              <article
                className="audit-card"
                key={event.id}
                data-testid={`audit-event-${event.id}`}
              >
                <div className="proposal-head">
                  <div className="proposal-title">
                    <strong>
                      #{event.id} {auditEventTypeLabel(event.event_type)}
                    </strong>
                    <span className="audit-type">{event.event_type}</span>
                  </div>
                  <span className="muted">
                    {formatDateTime(event.created_at)}
                  </span>
                </div>

                <EventHighlights event={event} />

                <div className="proposal-meta">
                  {event.proposal_id ? (
                    <span>提案 #{event.proposal_id}</span>
                  ) : null}
                  {event.conversation_id ? (
                    <span>会话 #{event.conversation_id}</span>
                  ) : null}
                </div>

                <details className="audit-details">
                  <summary>查看载荷与哈希</summary>
                  <pre className="audit-json">
                    {JSON.stringify(event.payload, null, 2)}
                  </pre>
                  <div className="hash-row">
                    <span>prev</span>
                    <code>{event.prev_hash}</code>
                  </div>
                  <div className="hash-row">
                    <span>row</span>
                    <code>{event.row_hash}</code>
                  </div>
                </details>
              </article>
            ))}
          </div>

          {hasMore ? (
            <button
              className="btn btn-ghost"
              type="button"
              disabled={loadingMore}
              onClick={() => void loadMore()}
            >
              {loadingMore ? "加载中" : "加载更多"}
            </button>
          ) : null}
        </>
      )}
    </section>
  );
}
