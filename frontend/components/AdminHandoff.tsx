"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { StatusBadge } from "@/components/StatusBadge";
import { api, errorMessage } from "@/lib/api";
import {
  formatDateTime,
  handoffIntentLabel,
  handoffPriorityLabel,
  handoffQueueLabel,
  handoffStatusLabel,
  orderStatusLabel,
} from "@/lib/status";
import type {
  HandoffDetail,
  HandoffList,
  HandoffTicket,
  QuickReply,
  StaffMember,
} from "@/lib/types";

type ReplyAttachment = {
  file: File;
  preview: string;
};

const FILTERS = [
  { value: "active", label: "待处理" },
  { value: "open", label: "待接管" },
  { value: "claimed", label: "处理中" },
  { value: "resolved", label: "已解决" },
  { value: "all", label: "全部" },
];

function statusTone(status: string): string {
  if (status === "open") return "badge-warn";
  if (status === "claimed") return "badge-neutral";
  return "badge-ok";
}

function priorityTone(priority: string): string {
  if (priority === "urgent") return "badge-danger";
  if (priority === "high") return "badge-warn";
  return "badge-neutral";
}

function messageAuthor(role: string): string {
  if (role === "user") return "顾客";
  if (role === "agent") return "客服";
  if (role === "system") return "系统";
  return "AI";
}

export function AdminHandoff({
  currentUserId,
  role,
}: {
  currentUserId: number;
  role: "owner" | "agent";
}) {
  const loadSeq = useRef(0);
  const replyFileInputs = useRef<Record<number, HTMLInputElement | null>>({});
  const [tickets, setTickets] = useState<HandoffTicket[]>([]);
  const [staff, setStaff] = useState<StaffMember[]>([]);
  const [assignTargets, setAssignTargets] = useState<Record<number, string>>({});
  const [counts, setCounts] = useState<Record<string, number>>({});
  const [status, setStatus] = useState("active");
  const [priority, setPriority] = useState("");
  const [searchInput, setSearchInput] = useState("");
  const [search, setSearch] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [notes, setNotes] = useState<Record<number, string>>({});
  const [replies, setReplies] = useState<Record<number, string>>({});
  const [replyFiles, setReplyFiles] = useState<
    Record<number, ReplyAttachment[]>
  >({});
  const [quickReplies, setQuickReplies] = useState<QuickReply[]>([]);
  const [internalNotes, setInternalNotes] = useState<
    Record<number, string>
  >({});
  const [noteBusyId, setNoteBusyId] = useState<number | null>(null);
  const [summaryBusyId, setSummaryBusyId] = useState<number | null>(null);
  const [expanded, setExpanded] = useState<number | null>(null);
  const [details, setDetails] = useState<Record<number, HandoffDetail>>({});

  const load = useCallback(async () => {
    const seq = ++loadSeq.current;
    setLoading(true);
    try {
      const params = new URLSearchParams({ status, limit: "50" });
      if (priority) params.set("priority", priority);
      if (search.trim()) params.set("q", search.trim());
      const body = await api<HandoffList>(
        `/api/admin/handoffs?${params.toString()}`,
      );
      if (seq !== loadSeq.current) return;
      setTickets(body.items);
      setCounts(body.counts);
      setError("");
    } catch (err) {
      if (seq === loadSeq.current) {
        setError(errorMessage(err));
      }
    } finally {
      if (seq === loadSeq.current) {
        setLoading(false);
      }
    }
  }, [status, priority, search]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (role !== "owner") return;
    api<StaffMember[]>("/api/admin/staff")
      .then((rows) => setStaff(rows.filter((row) => row.status === "active")))
      .catch((err) => setError(errorMessage(err)));
  }, [role]);

  useEffect(() => {
    api<QuickReply[]>("/api/admin/quick-replies")
      .then(setQuickReplies)
      .catch(() => setQuickReplies([]));
  }, []);

  const assignTarget = (ticket: HandoffTicket): string =>
    assignTargets[ticket.id] ??
    String(
      ticket.claimed_by_user_id ??
        staff.find((row) => row.role === "agent")?.id ??
        staff[0]?.id ??
        "",
    );

  const assign = async (ticket: HandoffTicket) => {
    const target = assignTarget(ticket);
    if (!target) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await api(`/api/admin/handoffs/${ticket.id}/assign`, {
        method: "POST",
        body: JSON.stringify({ user_id: Number(target) }),
      });
      setNotice(`工单 ${ticket.ticket_ref} 已指派`);
      await load();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  const loadDetail = async (ticket: HandoffTicket) => {
    if (expanded === ticket.id) {
      setExpanded(null);
      return;
    }
    setExpanded(ticket.id);
    if (details[ticket.id]) return;
    try {
      const detail = await api<HandoffDetail>(
        `/api/admin/handoffs/${ticket.id}`,
      );
      setDetails((previous) => ({ ...previous, [ticket.id]: detail }));
    } catch (err) {
      setError(errorMessage(err));
    }
  };

  const refreshSummary = async (ticket: HandoffTicket) => {
    setSummaryBusyId(ticket.id);
    setError("");
    setNotice("");
    try {
      const updated = await api<HandoffTicket>(
        `/api/admin/handoffs/${ticket.id}/summary`,
        { method: "POST" },
      );
      setTickets((previous) =>
        previous.map((row) => (row.id === ticket.id ? updated : row)),
      );
      setDetails((previous) =>
        previous[ticket.id]
          ? {
              ...previous,
              [ticket.id]: { ...previous[ticket.id], ...updated },
            }
          : previous,
      );
      setNotice(`工单 ${ticket.ticket_ref} 会话摘要已更新`);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setSummaryBusyId(null);
    }
  };

  const claim = async (ticket: HandoffTicket) => {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await api(`/api/admin/handoffs/${ticket.id}/claim`, { method: "POST" });
      setNotice(`工单 ${ticket.ticket_ref} 已接管`);
      setExpanded(null);
      setDetails({});
      await load();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  const addReplyFiles = (ticketId: number, files: FileList | null) => {
    if (!files) return;
    const current = replyFiles[ticketId] ?? [];
    const picked = Array.from(files).slice(0, Math.max(3 - current.length, 0));
    if (picked.length < files.length) {
      setError("每条回复最多 3 张图片");
    } else {
      setError("");
    }
    setReplyFiles((previous) => ({
      ...previous,
      [ticketId]: [
        ...(previous[ticketId] ?? []),
        ...picked.map((file) => ({
          file,
          preview: URL.createObjectURL(file),
        })),
      ].slice(0, 3),
    }));
  };

  const removeReplyFile = (ticketId: number, index: number) => {
    setReplyFiles((previous) => {
      const rows = [...(previous[ticketId] ?? [])];
      const removed = rows[index];
      if (removed) URL.revokeObjectURL(removed.preview);
      rows.splice(index, 1);
      return { ...previous, [ticketId]: rows };
    });
  };

  const sendReply = async (ticket: HandoffTicket) => {
    const content = (replies[ticket.id] ?? "").trim();
    const files = replyFiles[ticket.id] ?? [];
    if (!content && files.length === 0) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const attachmentIds: number[] = [];
      for (const item of files) {
        const form = new FormData();
        form.append("file", item.file);
        const uploaded = await api<{ id: number }>(
          `/api/admin/handoffs/${ticket.id}/attachments`,
          { method: "POST", body: form },
        );
        attachmentIds.push(uploaded.id);
      }
      const result = await api<{ auto_claimed: boolean }>(
        `/api/admin/handoffs/${ticket.id}/messages`,
        {
          method: "POST",
          body: JSON.stringify({ content, attachment_ids: attachmentIds }),
        },
      );
      setNotice(
        `已回复工单 ${ticket.ticket_ref}${result.auto_claimed ? "，并自动接管" : ""}`,
      );
      setReplies((previous) => ({ ...previous, [ticket.id]: "" }));
      files.forEach((item) => URL.revokeObjectURL(item.preview));
      setReplyFiles((previous) => ({ ...previous, [ticket.id]: [] }));
      const detail = await api<HandoffDetail>(
        `/api/admin/handoffs/${ticket.id}`,
      );
      setDetails((previous) => ({ ...previous, [ticket.id]: detail }));
      await load();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  const addNote = async (ticket: HandoffTicket) => {
    const content = (internalNotes[ticket.id] ?? "").trim();
    if (!content) return;
    setNoteBusyId(ticket.id);
    setError("");
    setNotice("");
    try {
      await api(`/api/admin/handoffs/${ticket.id}/notes`, {
        method: "POST",
        body: JSON.stringify({ content }),
      });
      setInternalNotes((previous) => ({
        ...previous,
        [ticket.id]: "",
      }));
      setNotice(`工单 ${ticket.ticket_ref} 内部备注已保存`);
      const detail = await api<HandoffDetail>(
        `/api/admin/handoffs/${ticket.id}`,
      );
      setDetails((previous) => ({ ...previous, [ticket.id]: detail }));
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setNoteBusyId(null);
    }
  };

  const resolve = async (ticket: HandoffTicket) => {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await api(`/api/admin/handoffs/${ticket.id}/resolve`, {
        method: "POST",
        body: JSON.stringify({ note: notes[ticket.id] ?? "" }),
      });
      setNotice(`工单 ${ticket.ticket_ref} 已标记解决`);
      setExpanded(null);
      setDetails({});
      await load();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="admin-handoff">
      <div className="audit-summary">
        <div className="handoff-counts">
          <span>待接管 {counts.open ?? 0}</span>
          <span>处理中 {counts.claimed ?? 0}</span>
          <span>已解决 {counts.resolved ?? 0}</span>
        </div>
        <span className="muted">客服可接管并处理；资金/地址提案仍需店主审批</span>
      </div>

      <div className="fulfillment-toolbar">
        <select
          value={status}
          onChange={(event) => {
            setStatus(event.target.value);
            setExpanded(null);
            setDetails({});
          }}
          aria-label="按工单状态筛选"
          data-testid="handoff-filter"
        >
          {FILTERS.map((item) => (
            <option key={item.value} value={item.value}>
              {item.label}
            </option>
          ))}
        </select>
        <select
          value={priority}
          onChange={(event) => {
            setPriority(event.target.value);
            setExpanded(null);
            setDetails({});
          }}
          aria-label="按工单优先级筛选"
          data-testid="handoff-priority-filter"
        >
          <option value="">全部优先级</option>
          <option value="urgent">紧急</option>
          <option value="high">高优先级</option>
          <option value="normal">普通</option>
        </select>
        <form
          className="search-form"
          onSubmit={(event) => {
            event.preventDefault();
            setSearch(searchInput);
          }}
        >
          <input
            value={searchInput}
            onChange={(event) => setSearchInput(event.target.value)}
            placeholder="搜索工单号、顾客或消息"
            maxLength={80}
            aria-label="搜索人工工单"
            data-testid="handoff-search"
          />
          <button className="btn btn-ghost btn-sm" type="submit">
            搜索
          </button>
        </form>
        <button
          className="btn btn-ghost btn-sm"
          type="button"
          onClick={() => void load()}
        >
          刷新
        </button>
      </div>

      {notice ? <div className="notice notice-success">{notice}</div> : null}
      {error ? <div className="notice notice-error">{error}</div> : null}

      {loading ? (
        <div className="loading">加载人工工单…</div>
      ) : tickets.length === 0 ? (
        <div className="empty-state">当前筛选下没有人工工单。</div>
      ) : (
        <div className="admin-grid">
          {tickets.map((ticket) => {
            const mine = ticket.claimed_by_user_id === currentUserId;
            const detail = details[ticket.id];
            return (
              <article
                className="handoff-card"
                key={ticket.id}
                data-testid={`handoff-${ticket.id}`}
              >
                <div className="proposal-head">
                  <div className="proposal-title">
                    <strong>{ticket.ticket_ref}</strong>
                    <StatusBadge
                      label={handoffStatusLabel(ticket.status)}
                      tone={statusTone(ticket.status)}
                    />
                    <StatusBadge
                      label={`${handoffQueueLabel(ticket.queue)}队列`}
                      tone="badge-neutral"
                    />
                    <StatusBadge
                      label={handoffPriorityLabel(ticket.priority)}
                      tone={priorityTone(ticket.priority)}
                    />
                    {ticket.escalated_at ? (
                      <StatusBadge
                        label="SLA 已升级"
                        tone="badge-danger"
                      />
                    ) : null}
                  </div>
                  <span className="muted">
                    {formatDateTime(ticket.updated_at)}
                  </span>
                </div>

                <div className="proposal-meta">
                  <span>
                    顾客 {ticket.customer_name ?? "未命名"} ·{" "}
                    {ticket.customer_phone ?? "—"}
                  </span>
                  <span>会话 #{ticket.conversation_id}</span>
                  <span>意图 {handoffIntentLabel(ticket.intent)}</span>
                  {ticket.claimed_by_email ? (
                    <span>处理人 {ticket.claimed_by_email}</span>
                  ) : null}
                </div>

                <div className="handoff-messages">
                  <p>
                    <strong>顾客</strong>
                    <span>{ticket.last_user_message || "—"}</span>
                  </p>
                  <p>
                    <strong>AI</strong>
                    <span>{ticket.last_reply || "—"}</span>
                  </p>
                </div>

                <div
                  className="handoff-summary"
                  data-testid={`handoff-summary-${ticket.id}`}
                >
                  <div className="handoff-summary-head">
                    <strong>会话摘要</strong>
                    <span className="muted">
                      {ticket.summary_generated_at
                        ? `更新于 ${formatDateTime(ticket.summary_generated_at)}`
                        : "尚未生成"}
                    </span>
                    <button
                      className="btn btn-ghost btn-sm"
                      type="button"
                      data-testid={`handoff-summary-refresh-${ticket.id}`}
                      disabled={summaryBusyId === ticket.id}
                      onClick={() => void refreshSummary(ticket)}
                    >
                      刷新摘要
                    </button>
                  </div>
                  <p className="handoff-summary-text">
                    {ticket.summary || "暂无摘要"}
                  </p>
                </div>

                {ticket.status === "resolved" ? (
                  <div className="proposal-meta">
                    <span>
                      解决人 {ticket.resolved_by_email ?? "客服"} ·{" "}
                      {formatDateTime(ticket.resolved_at)}
                    </span>
                    {ticket.resolution_note ? (
                      <span>说明 {ticket.resolution_note}</span>
                    ) : null}
                  </div>
                ) : null}

                <div className="proposal-actions">
                  <button
                    className="btn btn-ghost btn-sm"
                    type="button"
                    onClick={() => void loadDetail(ticket)}
                  >
                    {expanded === ticket.id ? "收起会话" : "查看会话"}
                  </button>

                  {role === "owner" && ticket.status !== "resolved" ? (
                    <>
                      <select
                        value={assignTarget(ticket)}
                        onChange={(event) =>
                          setAssignTargets((previous) => ({
                            ...previous,
                            [ticket.id]: event.target.value,
                          }))
                        }
                        aria-label={`工单 ${ticket.id} 指派客服`}
                        data-testid={`assignee-${ticket.id}`}
                      >
                        {staff.map((member) => (
                          <option key={member.id} value={member.id}>
                            {member.email}
                          </option>
                        ))}
                      </select>
                      <button
                        className="btn btn-ghost btn-sm"
                        type="button"
                        data-testid={`assign-${ticket.id}`}
                        disabled={busy || staff.length === 0}
                        onClick={() => void assign(ticket)}
                      >
                        {ticket.status === "claimed" ? "改派" : "指派"}
                      </button>
                    </>
                  ) : null}

                  {ticket.status === "open" ? (
                    <button
                      className="btn btn-primary btn-sm"
                      type="button"
                      data-testid={`claim-${ticket.id}`}
                      disabled={busy}
                      onClick={() => void claim(ticket)}
                    >
                      接管工单
                    </button>
                  ) : null}

                  {ticket.status === "claimed" && mine ? (
                    <>
                      <div className="reject-row">
                        <input
                          value={notes[ticket.id] ?? ""}
                          onChange={(event) =>
                            setNotes((previous) => ({
                              ...previous,
                              [ticket.id]: event.target.value,
                            }))
                          }
                          placeholder="处理说明（可选）"
                          maxLength={1000}
                          aria-label={`工单 ${ticket.id} 处理说明`}
                        />
                      </div>
                      <button
                        className="btn btn-primary btn-sm"
                        type="button"
                        data-testid={`resolve-${ticket.id}`}
                        disabled={busy}
                        onClick={() => void resolve(ticket)}
                      >
                        标记解决
                      </button>
                    </>
                  ) : null}

                  {ticket.status === "claimed" && !mine ? (
                    <span className="muted">
                      由 {ticket.claimed_by_email ?? "其他客服"} 处理中
                    </span>
                  ) : null}
                </div>

                {expanded === ticket.id ? (
                  <div className="handoff-thread">
                    {!detail ? (
                      <div className="loading">加载会话…</div>
                    ) : (
                      <>
                      <div
                        className="handoff-context"
                        data-testid="handoff-context"
                      >
                        <div className="proposal-meta">
                          <span>
                            历史订单 {detail.customer_context.order_count}
                          </span>
                          <span>
                            售后{" "}
                            {detail.customer_context.after_sales_count}
                          </span>
                          <span>
                            待审提案{" "}
                            {detail.customer_context.pending_proposals}
                          </span>
                          <span>
                            进行中退货{" "}
                            {detail.customer_context.active_returns}
                          </span>
                          {detail.customer_context.last_order_at ? (
                            <span>
                              最近下单{" "}
                              {formatDateTime(
                                detail.customer_context.last_order_at,
                              )}
                            </span>
                          ) : null}
                        </div>
                        {detail.customer_context.recent_orders.length > 0 ? (
                          <div className="handoff-context-orders">
                            {detail.customer_context.recent_orders.map(
                              (order) => (
                                <span key={order.order_no}>
                                  {order.order_no} ·{" "}
                                  {orderStatusLabel(order.status)} ·{" "}
                                  {order.payable_display} 元
                                </span>
                              ),
                            )}
                          </div>
                        ) : null}
                      </div>
                      {detail.messages.map((message) => (
                        <div
                          className={`handoff-msg ${message.role}`}
                          key={message.id}
                        >
                          <div className="handoff-msg-head">
                            <strong>
                              {messageAuthor(message.role)}
                            </strong>
                            <span>{formatDateTime(message.created_at)}</span>
                          </div>
                          {message.content ? <p>{message.content}</p> : null}
                          {message.image_urls.length > 0 ? (
                            <div
                              className="handoff-msg-images"
                              data-testid={`handoff-images-${message.id}`}
                            >
                              {message.image_urls.map((url) => (
                                <a
                                  href={url}
                                  key={url}
                                  target="_blank"
                                  rel="noreferrer"
                                >
                                  {/* eslint-disable-next-line @next/next/no-img-element */}
                                  <img
                                    className="handoff-msg-image"
                                    src={url}
                                    alt="顾客上传的图片"
                                  />
                                </a>
                              ))}
                            </div>
                          ) : null}
                        </div>
                      ))}
                      </>
                    )}
                    {detail ? (
                      <div
                        className="handoff-notes"
                        data-testid={`handoff-notes-${ticket.id}`}
                      >
                        <h4>内部备注（顾客不可见）</h4>
                        {detail.notes.length === 0 ? (
                          <span className="muted">暂无内部备注。</span>
                        ) : (
                          detail.notes.map((note) => (
                            <div
                              className="handoff-note"
                              key={note.id}
                              data-testid={`handoff-note-${note.id}`}
                            >
                              <div className="handoff-msg-head">
                                <strong>
                                  {note.author_email ?? "客服"}
                                </strong>
                                <span>
                                  {formatDateTime(note.created_at)}
                                </span>
                              </div>
                              <p>{note.content}</p>
                            </div>
                          ))
                        )}
                        <form
                          className="handoff-note-form"
                          onSubmit={(event) => {
                            event.preventDefault();
                            void addNote(ticket);
                          }}
                        >
                          <input
                            value={internalNotes[ticket.id] ?? ""}
                            onChange={(event) =>
                              setInternalNotes((previous) => ({
                                ...previous,
                                [ticket.id]: event.target.value,
                              }))
                            }
                            placeholder="内部备注（1000 字以内）"
                            maxLength={1000}
                            aria-label={`工单 ${ticket.id} 内部备注`}
                            data-testid={`handoff-note-input-${ticket.id}`}
                          />
                          <button
                            className="btn btn-ghost btn-sm"
                            type="submit"
                            disabled={
                              noteBusyId === ticket.id ||
                              !(internalNotes[ticket.id] ?? "").trim()
                            }
                            data-testid={`handoff-note-send-${ticket.id}`}
                          >
                            保存备注
                          </button>
                        </form>
                      </div>
                    ) : null}
                    {ticket.status !== "resolved" &&
                    (ticket.status === "open" || mine) ? (
                      <form
                        className="handoff-reply"
                        onSubmit={(event) => {
                          event.preventDefault();
                          void sendReply(ticket);
                        }}
                      >
                        {(replyFiles[ticket.id] ?? []).length > 0 ? (
                          <div
                            className="handoff-reply-attachments"
                            data-testid={`handoff-reply-attachments-${ticket.id}`}
                          >
                            {(replyFiles[ticket.id] ?? []).map(
                              (item, index) => (
                                <div
                                  className="chat-attachment"
                                  key={`${item.file.name}-${index}`}
                                >
                                  {/* eslint-disable-next-line @next/next/no-img-element */}
                                  <img
                                    className="chat-image"
                                    src={item.preview}
                                    alt={item.file.name}
                                  />
                                  <button
                                    type="button"
                                    className="chat-attachment-remove"
                                    aria-label={`移除 ${item.file.name}`}
                                    onClick={() =>
                                      removeReplyFile(ticket.id, index)
                                    }
                                  >
                                    ×
                                  </button>
                                </div>
                              ),
                            )}
                          </div>
                        ) : null}
                        <input
                          ref={(node) => {
                            replyFileInputs.current[ticket.id] = node;
                          }}
                          id={`handoff-reply-files-${ticket.id}`}
                          type="file"
                          accept="image/png,image/jpeg,image/webp"
                          multiple
                          hidden
                          data-testid={`handoff-reply-files-${ticket.id}`}
                          onChange={(event) => {
                            addReplyFiles(ticket.id, event.target.files);
                            event.target.value = "";
                          }}
                        />
                        {quickReplies.length > 0 ? (
                          <select
                            value=""
                            aria-label="套用话术模板"
                            data-testid={`quick-reply-select-${ticket.id}`}
                            onChange={(event) => {
                              const selected = quickReplies.find(
                                (item) =>
                                  item.id === Number(event.target.value),
                              );
                              if (selected) {
                                setReplies((previous) => ({
                                  ...previous,
                                  [ticket.id]: selected.content,
                                }));
                              }
                            }}
                          >
                            <option value="">套用话术…</option>
                            {quickReplies.map((item) => (
                              <option key={item.id} value={item.id}>
                                {item.category
                                  ? `${item.category} · ${item.title}`
                                  : item.title}
                              </option>
                            ))}
                          </select>
                        ) : null}
                        <input
                          value={replies[ticket.id] ?? ""}
                          onChange={(event) =>
                            setReplies((previous) => ({
                              ...previous,
                              [ticket.id]: event.target.value,
                            }))
                          }
                          placeholder="回复顾客（1000 字以内）"
                          maxLength={1000}
                          aria-label={`回复工单 ${ticket.id}`}
                          data-testid={`handoff-reply-${ticket.id}`}
                        />
                        <button
                          className="btn btn-ghost btn-sm"
                          type="button"
                          disabled={
                            busy ||
                            (replyFiles[ticket.id] ?? []).length >= 3
                          }
                          onClick={() =>
                            replyFileInputs.current[ticket.id]?.click()
                          }
                          data-testid={`handoff-reply-image-button-${ticket.id}`}
                        >
                          图片
                          {(replyFiles[ticket.id] ?? []).length > 0
                            ? ` ${(replyFiles[ticket.id] ?? []).length}/3`
                            : ""}
                        </button>
                        <button
                          className="btn btn-primary btn-sm"
                          type="submit"
                          disabled={
                            busy ||
                            (!(replies[ticket.id] ?? "").trim() &&
                              (replyFiles[ticket.id] ?? []).length === 0)
                          }
                          data-testid={`handoff-send-${ticket.id}`}
                        >
                          发送回复
                        </button>
                      </form>
                    ) : null}
                  </div>
                ) : null}
              </article>
            );
          })}
        </div>
      )}
    </section>
  );
}
