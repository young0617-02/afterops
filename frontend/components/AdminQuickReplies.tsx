"use client";

import { useCallback, useEffect, useState, type FormEvent } from "react";

import { StatusBadge } from "@/components/StatusBadge";
import { api, errorMessage } from "@/lib/api";
import { formatDateTime } from "@/lib/status";
import type { QuickReply } from "@/lib/types";

export function AdminQuickReplies() {
  const [replies, setReplies] = useState<QuickReply[]>([]);
  const [title, setTitle] = useState("");
  const [category, setCategory] = useState("");
  const [content, setContent] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setReplies(
        await api<QuickReply[]>(
          "/api/admin/quick-replies?include_disabled=true",
        ),
      );
      setError("");
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const create = async (event: FormEvent) => {
    event.preventDefault();
    if (!title.trim() || !content.trim()) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const created = await api<QuickReply>("/api/admin/quick-replies", {
        method: "POST",
        body: JSON.stringify({
          title: title.trim(),
          category: category.trim(),
          content: content.trim(),
        }),
      });
      setNotice(`话术「${created.title}」已创建`);
      setTitle("");
      setCategory("");
      setContent("");
      await load();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  const toggle = async (reply: QuickReply) => {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const next = reply.status === "active" ? "disabled" : "active";
      const updated = await api<QuickReply>(
        `/api/admin/quick-replies/${reply.id}`,
        {
          method: "PATCH",
          body: JSON.stringify({ status: next }),
        },
      );
      setNotice(
        `话术「${updated.title}」已${next === "active" ? "启用" : "停用"}`,
      );
      await load();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="admin-quick-replies">
      <section className="panel panel-pad">
        <div className="section-head" style={{ marginBottom: 10 }}>
          <div>
            <h3 style={{ margin: 0 }}>话术模板</h3>
            <p>
              店主维护共享话术；客服在工单会话中可一键套用并继续编辑。
            </p>
          </div>
        </div>
        {notice ? <div className="notice notice-success">{notice}</div> : null}
        {error ? <div className="notice notice-error">{error}</div> : null}
        <form className="quick-reply-form" onSubmit={create}>
          <div className="field">
            <label htmlFor="quick-reply-title">标题</label>
            <input
              id="quick-reply-title"
              data-testid="quick-reply-title"
              value={title}
              onChange={(event) => setTitle(event.target.value)}
              maxLength={120}
              required
            />
          </div>
          <div className="field">
            <label htmlFor="quick-reply-category">分类（可选）</label>
            <input
              id="quick-reply-category"
              data-testid="quick-reply-category"
              value={category}
              onChange={(event) => setCategory(event.target.value)}
              maxLength={80}
            />
          </div>
          <div className="field full">
            <label htmlFor="quick-reply-content">内容</label>
            <textarea
              id="quick-reply-content"
              data-testid="quick-reply-content"
              value={content}
              onChange={(event) => setContent(event.target.value)}
              maxLength={1000}
              rows={3}
              required
            />
          </div>
          <button
            className="btn btn-primary"
            type="submit"
            data-testid="create-quick-reply"
            disabled={busy || !title.trim() || !content.trim()}
          >
            创建话术
          </button>
        </form>
      </section>

      {loading ? (
        <div className="loading">加载话术…</div>
      ) : replies.length === 0 ? (
        <div className="empty-state">还没有话术模板。</div>
      ) : (
        <div className="admin-grid">
          {replies.map((reply) => (
            <article
              className="proposal-card"
              key={reply.id}
              data-testid={`quick-reply-${reply.id}`}
            >
              <div className="proposal-head">
                <div className="proposal-title">
                  <strong>{reply.title}</strong>
                  <StatusBadge
                    label={reply.status === "active" ? "启用" : "已停用"}
                    tone={
                      reply.status === "active" ? "badge-ok" : "badge-muted"
                    }
                  />
                </div>
                <span className="muted">
                  {formatDateTime(reply.updated_at)}
                </span>
              </div>
              <div className="proposal-meta">
                <span>分类 {reply.category || "—"}</span>
                {reply.created_by_email ? (
                  <span>创建人 {reply.created_by_email}</span>
                ) : null}
              </div>
              <p className="quick-reply-content">{reply.content}</p>
              <div className="proposal-actions">
                <button
                  className="btn btn-ghost btn-sm"
                  type="button"
                  disabled={busy}
                  data-testid={`quick-reply-toggle-${reply.id}`}
                  onClick={() => void toggle(reply)}
                >
                  {reply.status === "active" ? "停用" : "启用"}
                </button>
              </div>
            </article>
          ))}
        </div>
      )}
    </section>
  );
}