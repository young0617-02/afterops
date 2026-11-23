"use client";

import { useCallback, useEffect, useState } from "react";

import { StatusBadge } from "@/components/StatusBadge";
import { api, errorMessage } from "@/lib/api";
import { formatDateTime } from "@/lib/status";
import type { AdminReview } from "@/lib/types";

const FILTERS = [
  { value: "all", label: "全部" },
  { value: "visible", label: "展示中" },
  { value: "hidden", label: "已隐藏" },
];

export function AdminReviews({ role }: { role: "owner" | "agent" }) {
  const [items, setItems] = useState<AdminReview[]>([]);
  const [status, setStatus] = useState("all");
  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [replyDrafts, setReplyDrafts] = useState<Record<number, string>>({});
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setItems(
        await api<AdminReview[]>(
          `/api/admin/reviews?status=${status}`,
        ),
      );
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

  const toggle = async (review: AdminReview) => {
    setBusyId(review.id);
    setError("");
    setNotice("");
    try {
      const next = review.status === "visible" ? "hidden" : "visible";
      await api(`/api/admin/reviews/${review.id}`, {
        method: "PATCH",
        body: JSON.stringify({ status: next }),
      });
      setNotice(
        `评价 #${review.id} 已${next === "hidden" ? "隐藏" : "恢复展示"}`,
      );
      await load();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusyId(null);
    }
  };

  const saveReply = async (review: AdminReview) => {
    setBusyId(review.id);
    setError("");
    setNotice("");
    try {
      const reply = replyDrafts[review.id] ?? review.merchant_reply;
      const updated = await api<AdminReview>(
        `/api/admin/reviews/${review.id}/reply`,
        {
          method: "PATCH",
          body: JSON.stringify({ reply }),
        },
      );
      setNotice(
        updated.merchant_reply
          ? `评价 #${review.id} 回复已保存`
          : `评价 #${review.id} 回复已清空`,
      );
      setReplyDrafts((previous) => {
        const next = { ...previous };
        delete next[review.id];
        return next;
      });
      await load();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusyId(null);
    }
  };

  return (
    <section className="admin-reviews">
      <div className="audit-summary">
        <span className="muted">
          商品评价仅来自已签收订单；隐藏后不会出现在商品页。
        </span>
      </div>
      <div className="fulfillment-toolbar">
        <select
          value={status}
          onChange={(event) => setStatus(event.target.value)}
          aria-label="按评价状态筛选"
          data-testid="review-status-filter"
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
          客服账号只能查看评价；隐藏/恢复评价需要店主权限。
        </div>
      ) : null}
      {notice ? <div className="notice notice-success">{notice}</div> : null}
      {error ? <div className="notice notice-error">{error}</div> : null}

      {loading ? (
        <div className="loading">加载评价…</div>
      ) : items.length === 0 ? (
        <div className="empty-state">当前筛选下没有评价。</div>
      ) : (
        <div className="admin-grid">
          {items.map((review) => (
            <article
              className="proposal-card"
              key={review.id}
              data-testid={`admin-review-${review.id}`}
            >
              <div className="proposal-head">
                <div className="proposal-title">
                  <strong>
                    {"★".repeat(review.rating)}
                    {"☆".repeat(5 - review.rating)}
                  </strong>
                  <StatusBadge
                    label={review.status === "visible" ? "展示中" : "已隐藏"}
                    tone={
                      review.status === "visible"
                        ? "badge-ok"
                        : "badge-muted"
                    }
                  />
                </div>
                <span className="muted">
                  {formatDateTime(review.created_at)}
                </span>
              </div>
              <div className="proposal-meta">
                <span>
                  {review.product_title} · {review.sku}
                </span>
                <span>订单 {review.order_no}</span>
                <span>
                  {review.customer_name} · {review.customer_phone ?? "—"}
                </span>
              </div>
              {review.content ? (
                <p className="after-sales-reason">{review.content}</p>
              ) : null}
              {review.image_urls.length > 0 ? (
                <div className="review-images">
                  {review.image_urls.map((url) => (
                    <a
                      href={url}
                      target="_blank"
                      rel="noreferrer"
                      key={url}
                    >
                      <img
                        src={url}
                        alt="评价图片"
                        className="review-image"
                        loading="lazy"
                      />
                    </a>
                  ))}
                </div>
              ) : null}
              {review.follow_up_content ? (
                <div className="review-follow-up">
                  <strong>追评</strong>
                  <p>{review.follow_up_content}</p>
                  {review.follow_up_image_urls.length > 0 ? (
                    <div className="review-images">
                      {review.follow_up_image_urls.map((url) => (
                        <a
                          href={url}
                          target="_blank"
                          rel="noreferrer"
                          key={url}
                        >
                          <img
                            src={url}
                            alt="追评图片"
                            className="review-image"
                            loading="lazy"
                          />
                        </a>
                      ))}
                    </div>
                  ) : null}
                </div>
              ) : null}
              {review.merchant_reply ? (
                <div className="merchant-reply">
                  <strong>商家回复</strong>
                  <p>{review.merchant_reply}</p>
                </div>
              ) : null}
              {role === "owner" ? (
                <div className="review-reply-editor">
                  <textarea
                    value={
                      replyDrafts[review.id] ?? review.merchant_reply
                    }
                    onChange={(event) =>
                      setReplyDrafts((previous) => ({
                        ...previous,
                        [review.id]: event.target.value,
                      }))
                    }
                    placeholder="回复顾客（1000 字以内，留空可清空）"
                    maxLength={1000}
                    rows={2}
                    data-testid={`review-reply-${review.id}`}
                  />
                  <button
                    className="btn btn-ghost btn-sm"
                    type="button"
                    disabled={busyId === review.id}
                    data-testid={`save-review-reply-${review.id}`}
                    onClick={() => void saveReply(review)}
                  >
                    保存回复
                  </button>
                </div>
              ) : null}
              {role === "owner" ? (
                <div className="proposal-actions">
                  <button
                    className={
                      review.status === "visible"
                        ? "btn btn-danger btn-sm"
                        : "btn btn-primary btn-sm"
                    }
                    type="button"
                    data-testid={`toggle-review-${review.id}`}
                    disabled={busyId === review.id}
                    onClick={() => void toggle(review)}
                  >
                    {review.status === "visible" ? "隐藏评价" : "恢复展示"}
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
