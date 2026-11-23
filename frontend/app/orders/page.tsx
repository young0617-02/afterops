"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { StatusBadge } from "@/components/StatusBadge";
import { useCustomer } from "@/components/CustomerProvider";
import { api, errorMessage } from "@/lib/api";
import {
  formatDateTime,
  invoiceStatusLabel,
  orderStatusLabel,
  paymentStatusLabel,
  shipmentStatusLabel,
} from "@/lib/status";
import type {
  CancelRequestResult,
  InvoiceView,
  MyReview,
  OrderDetail,
  OrderSummary,
} from "@/lib/types";

export default function OrdersPage() {
  const { customer } = useCustomer();
  const [orders, setOrders] = useState<OrderSummary[]>([]);
  const [statusFilter, setStatusFilter] = useState("");
  const [searchInput, setSearchInput] = useState("");
  const [query, setQuery] = useState("");
  const [hasMore, setHasMore] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [details, setDetails] = useState<Record<string, OrderDetail>>({});
  const [expanded, setExpanded] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [myReviews, setMyReviews] = useState<MyReview[]>([]);
  const [reviewForms, setReviewForms] = useState<
    Record<string, { rating: string; content: string }>
  >({});
  const [reviewFiles, setReviewFiles] = useState<Record<string, File[]>>({});
  const [reviewBusyKey, setReviewBusyKey] = useState<string | null>(null);
  const [partialCancelFor, setPartialCancelFor] = useState<string | null>(
    null,
  );
  const [partialCancelQty, setPartialCancelQty] = useState<
    Record<string, Record<string, string>>
  >({});
  const [followUpForms, setFollowUpForms] = useState<Record<string, string>>(
    {},
  );
  const [followUpFiles, setFollowUpFiles] = useState<
    Record<string, File[]>
  >({});
  const [followUpBusyKey, setFollowUpBusyKey] = useState<string | null>(null);
  const [invoices, setInvoices] = useState<InvoiceView[]>([]);
  const [invoiceOpenFor, setInvoiceOpenFor] = useState<string | null>(null);
  const [invoiceForms, setInvoiceForms] = useState<
    Record<
      string,
      {
        invoice_type: "personal" | "company";
        title: string;
        tax_no: string;
        email: string;
      }
    >
  >({});
  const [invoiceBusy, setInvoiceBusy] = useState(false);

  const loadOrders = useCallback(
    async (append = false) => {
      if (!customer) {
        setOrders([]);
        setHasMore(false);
        return;
      }
      if (append) setLoadingMore(true);
      else setLoading(true);
      try {
        const params = new URLSearchParams({ limit: "20" });
        if (statusFilter) params.set("status", statusFilter);
        if (query.trim()) params.set("q", query.trim());
        if (append && orders.length > 0) {
          params.set("before_id", String(orders[orders.length - 1].id));
        }
        const rows = await api<OrderSummary[]>(
          `/api/store/orders?${params.toString()}`,
        );
        if (append) {
          setOrders((previous) => [...previous, ...rows]);
        } else {
          setOrders(rows);
        }
        setHasMore(rows.length === 20);
        setError("");
      } catch (err) {
        setError(errorMessage(err));
      } finally {
        if (append) setLoadingMore(false);
        else setLoading(false);
      }
    },
    [customer, statusFilter, query, orders],
  );

  useEffect(() => {
    void loadOrders();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [customer, statusFilter, query]);

  const loadMyReviews = useCallback(async () => {
    if (!customer) {
      setMyReviews([]);
      return;
    }
    try {
      setMyReviews(await api<MyReview[]>("/api/store/reviews/mine"));
    } catch {
      setMyReviews([]);
    }
  }, [customer]);

  useEffect(() => {
    void loadMyReviews();
  }, [loadMyReviews]);

  const loadInvoices = useCallback(async () => {
    if (!customer) {
      setInvoices([]);
      return;
    }
    try {
      setInvoices(await api<InvoiceView[]>("/api/store/invoices"));
    } catch {
      setInvoices([]);
    }
  }, [customer]);

  useEffect(() => {
    void loadInvoices();
  }, [loadInvoices]);

  const invoiceMap = invoices.reduce<Record<string, InvoiceView>>(
    (latest, invoice) => {
      const current = latest[invoice.order_no];
      if (!current || invoice.id > current.id) {
        latest[invoice.order_no] = invoice;
      }
      return latest;
    },
    {},
  );

  const submitInvoice = async (orderNo: string) => {
    const form = invoiceForms[orderNo] ?? {
      invoice_type: "personal" as const,
      title: "",
      tax_no: "",
      email: "",
    };
    if (!form.title.trim()) {
      setError("请填写发票抬头");
      return;
    }
    if (form.invoice_type === "company" && !form.tax_no.trim()) {
      setError("企业发票必须填写税号");
      return;
    }
    setInvoiceBusy(true);
    setError("");
    setNotice("");
    try {
      await api("/api/store/invoices", {
        method: "POST",
        body: JSON.stringify({
          order_no: orderNo,
          invoice_type: form.invoice_type,
          title: form.title.trim(),
          tax_no: form.tax_no.trim(),
          email: form.email.trim(),
        }),
      });
      setNotice("发票申请已提交，等待店主开票");
      setInvoiceOpenFor(null);
      await loadInvoices();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setInvoiceBusy(false);
    }
  };

  const invoiceFormFor = (orderNo: string) =>
    invoiceForms[orderNo] ?? {
      invoice_type: "personal" as const,
      title: customer?.name ?? "",
      tax_no: "",
      email: customer?.email ?? "",
    };

  const patchInvoiceForm = (
    orderNo: string,
    patch: Partial<ReturnType<typeof invoiceFormFor>>,
  ) => {
    setInvoiceForms((previous) => ({
      ...previous,
      [orderNo]: { ...invoiceFormFor(orderNo), ...patch },
    }));
  };

  const reviewMap = Object.fromEntries(
    myReviews.map((review) => [
      `${review.order_no}:${review.sku}`,
      review,
    ]),
  );

  const uploadReviewImages = async (files: File[]): Promise<number[]> => {
    const ids: number[] = [];
    for (const file of files.slice(0, 3)) {
      const data = new FormData();
      data.append("file", file);
      const uploaded = await api<{ id: number }>("/api/store/attachments", {
        method: "POST",
        body: data,
      });
      ids.push(uploaded.id);
    }
    return ids;
  };

  const submitReview = async (orderNo: string, sku: string) => {
    const key = `${orderNo}:${sku}`;
    const form = reviewForms[key] ?? { rating: "", content: "" };
    if (!form.rating) {
      setError("请选择 1–5 星评分");
      return;
    }
    setReviewBusyKey(key);
    setError("");
    setNotice("");
    try {
      const attachmentIds = await uploadReviewImages(
        reviewFiles[key] ?? [],
      );
      await api("/api/store/reviews", {
        method: "POST",
        body: JSON.stringify({
          order_no: orderNo,
          sku,
          rating: Number(form.rating),
          content: form.content,
          attachment_ids: attachmentIds,
        }),
      });
      setNotice(`已提交 ${form.rating} 星评价`);
      setReviewFiles((previous) => ({ ...previous, [key]: [] }));
      await loadMyReviews();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setReviewBusyKey(null);
    }
  };

  const submitFollowUp = async (
    orderNo: string,
    sku: string,
    reviewId: number,
  ) => {
    const key = `${orderNo}:${sku}`;
    const content = (followUpForms[key] ?? "").trim();
    if (!content) {
      setError("请填写追评内容");
      return;
    }
    setFollowUpBusyKey(key);
    setError("");
    setNotice("");
    try {
      const attachmentIds = await uploadReviewImages(
        (followUpFiles[key] ?? []).slice(0, 3),
      );
      await api(`/api/store/reviews/${reviewId}/follow-up`, {
        method: "POST",
        body: JSON.stringify({ content, attachment_ids: attachmentIds }),
      });
      setNotice("追评已提交");
      setFollowUpForms((previous) => ({ ...previous, [key]: "" }));
      setFollowUpFiles((previous) => ({ ...previous, [key]: [] }));
      await loadMyReviews();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setFollowUpBusyKey(null);
    }
  };

  const loadDetail = async (orderNo: string) => {
    try {
      const detail = await api<OrderDetail>(
        `/api/store/orders/${encodeURIComponent(orderNo)}`,
      );
      setDetails((previous) => ({ ...previous, [orderNo]: detail }));
    } catch (err) {
      setError(errorMessage(err));
    }
  };

  const toggle = async (orderNo: string) => {
    if (expanded === orderNo) {
      setExpanded(null);
      return;
    }
    setExpanded(orderNo);
    if (!details[orderNo]) {
      await loadDetail(orderNo);
    }
  };

  const cancelOrder = async (orderNo: string) => {
    setBusy(true);
    setError("");
    try {
      await api(`/api/store/orders/${encodeURIComponent(orderNo)}/cancel`, {
        method: "POST",
      });
      setNotice(`订单 ${orderNo} 已取消，库存已释放`);
      await loadOrders();
      if (expanded === orderNo) {
        await loadDetail(orderNo);
      }
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  const requestCancellation = async (
    orderNo: string,
    items?: { sku: string; qty: number }[],
  ) => {
    setBusy(true);
    setError("");
    try {
      const result = await api<CancelRequestResult>(
        `/api/store/orders/${encodeURIComponent(orderNo)}/cancel-request`,
        {
          method: "POST",
          ...(items ? { body: JSON.stringify({ items }) } : {}),
        },
      );
      setNotice(
        items
          ? `订单 ${orderNo} 已提交取消申请，预计退款 ${result.amount_display} 元，等待店主审批`
          : `订单 ${orderNo} 取消申请已提交，等待店主审批`,
      );
      setPartialCancelFor(null);
      setPartialCancelQty((previous) => {
        const next = { ...previous };
        delete next[orderNo];
        return next;
      });
      await loadOrders();
      if (expanded === orderNo) {
        await loadDetail(orderNo);
      }
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  const updatePartialCancelQty = (
    orderNo: string,
    sku: string,
    value: string,
  ) => {
    setPartialCancelQty((previous) => ({
      ...previous,
      [orderNo]: { ...(previous[orderNo] ?? {}), [sku]: value },
    }));
  };

  const submitPartialCancellation = (orderNo: string) => {
    const detail = details[orderNo];
    if (!detail) return;
    const form = partialCancelQty[orderNo] ?? {};
    const items = detail.items
      .filter((item) => item.cancelable_qty > 0)
      .map((item) => ({
        sku: item.sku,
        qty: Number(form[item.sku] ?? 0),
      }))
      .filter((item) => Number.isFinite(item.qty) && item.qty > 0);
    if (items.length === 0) {
      setError("请填写至少一个商品的取消数量");
      return;
    }
    void requestCancellation(orderNo, items);
  };

  const confirmReceipt = async (orderNo: string) => {
    setBusy(true);
    setError("");
    try {
      await api(
        `/api/store/orders/${encodeURIComponent(orderNo)}/confirm-receipt`,
        { method: "POST" },
      );
      setNotice(`订单 ${orderNo} 已确认收货`);
      await loadOrders();
      if (expanded === orderNo) {
        await loadDetail(orderNo);
      }
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  if (!customer) {
    return (
      <div className="empty-state">
        请先在右上角用手机号登录，再查看订单。
      </div>
    );
  }

  return (
    <>
      <div className="section-head">
        <div>
          <h2>我的订单</h2>
          <p>订单状态、支付、物流与收货信息都来自交易系统。</p>
        </div>
        <div className="card-actions">
          <form
            className="proposal-search"
            onSubmit={(event) => {
              event.preventDefault();
              setQuery(searchInput);
            }}
          >
            <input
              value={searchInput}
              onChange={(event) => setSearchInput(event.target.value)}
              placeholder="搜索订单号"
              maxLength={32}
              aria-label="搜索订单号"
              data-testid="order-search"
            />
            <button className="btn btn-ghost btn-sm" type="submit">
              搜索
            </button>
          </form>
          <select
            value={statusFilter}
            onChange={(event) => setStatusFilter(event.target.value)}
            aria-label="按订单状态筛选"
            data-testid="order-status-filter"
          >
            <option value="">全部状态</option>
            <option value="pending_payment">待支付</option>
            <option value="paid">已支付</option>
            <option value="shipped">已发货</option>
            <option value="delivered">已签收</option>
            <option value="completed">已完成</option>
            <option value="refunded">已退款</option>
            <option value="cancelled">已取消</option>
          </select>
          <Link className="btn btn-ghost" href="/">
            继续购物
          </Link>
        </div>
      </div>

      {notice ? <div className="notice notice-success">{notice}</div> : null}
      {error ? <div className="notice notice-error">{error}</div> : null}

      {loading ? (
        <div className="loading">加载订单…</div>
      ) : orders.length === 0 ? (
        <div className="empty-state">
          {statusFilter ? "当前筛选下没有订单。" : "还没有订单。"}
        </div>
      ) : (
        <div className="order-list">
          {orders.map((order) => {
            const detail = details[order.order_no];
            const isOpen = expanded === order.order_no;
            const statusLabel = orderStatusLabel(order.status);
            const paymentLabel = paymentStatusLabel(order.payment_status);
            return (
              <article
                className="order-card"
                key={order.order_no}
                data-testid={`order-${order.order_no}`}
              >
                <div className="order-head">
                  <div>
                    <div className="order-title">
                      <strong>{order.order_no}</strong>
                      <StatusBadge label={statusLabel} status={order.status} />
                      {paymentLabel !== statusLabel ? (
                        <StatusBadge
                          label={paymentLabel}
                          status={order.payment_status}
                        />
                      ) : null}
                    </div>
                    <div className="order-sub">
                      <span>{formatDateTime(order.placed_at)}</span>
                      <span>{order.item_count} 件商品</span>
                      <span>应付 {order.payable_display} 元</span>
                    </div>
                  </div>
                  <div className="card-actions">
                    {order.status === "pending_payment" ? (
                      <button
                        className="btn btn-danger btn-sm"
                        type="button"
                        disabled={busy}
                        onClick={() => void cancelOrder(order.order_no)}
                      >
                        取消订单
                      </button>
                    ) : null}
                    {order.status === "paid" &&
                    order.payment_status === "paid" &&
                    order.fulfillment_status === "unfulfilled" ? (
                      order.cancel_requested_at ? (
                        <span className="badge badge-warn">
                          取消申请待审核
                        </span>
                      ) : (
                        <button
                          className="btn btn-danger btn-sm"
                          type="button"
                          disabled={busy}
                          data-testid={`request-cancel-${order.order_no}`}
                          onClick={() =>
                            void requestCancellation(order.order_no)
                          }
                        >
                          申请取消
                        </button>
                      )
                    ) : null}
                    {order.status === "delivered" ? (
                      <button
                        className="btn btn-primary btn-sm"
                        type="button"
                        disabled={busy}
                        onClick={() => void confirmReceipt(order.order_no)}
                      >
                        确认收货
                      </button>
                    ) : null}
                    <button
                      className="btn btn-ghost btn-sm"
                      type="button"
                      onClick={() => void toggle(order.order_no)}
                    >
                      {isOpen ? "收起" : "查看详情"}
                    </button>
                  </div>
                </div>

                {isOpen ? (
                  <div className="order-body">
                    {!detail ? (
                      <div className="loading">加载详情…</div>
                    ) : (
                      <>
                        <div className="detail-box">
                          <h4>商品</h4>
                          <table className="table">
                            <thead>
                              <tr>
                                <th>SKU</th>
                                <th>商品</th>
                                <th>数量</th>
                                <th>已取消</th>
                                <th>单价</th>
                                <th>小计</th>
                              </tr>
                            </thead>
                            <tbody>
                              {detail.items.map((item) => (
                                <tr key={item.sku}>
                                  <td>{item.sku}</td>
                                  <td>{item.title}</td>
                                  <td>{item.qty}</td>
                                  <td>
                                    {item.cancelled_qty > 0
                                      ? item.cancelled_qty
                                      : "—"}
                                  </td>
                                  <td>{item.unit_price_display}</td>
                                  <td>{item.line_total_display}</td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                          {detail.discount_cents > 0 ||
                          detail.freight_cents > 0 ? (
                            <p className="muted">
                              运费 {detail.freight_display} 元 · 优惠 -
                              {detail.discount_display} 元 · 应付{" "}
                              {detail.payable_display} 元
                            </p>
                          ) : null}
                          {order.cancel_requested_at ? (
                            <span className="badge badge-warn">
                              取消申请待审核
                            </span>
                          ) : null}
                          {(() => {
                            const cancelableItems = detail.items.filter(
                              (item) => item.cancelable_qty > 0,
                            );
                            const allowed =
                              cancelableItems.length > 0 &&
                              [
                                "paid",
                                "fulfilling",
                                "shipped",
                                "partially_refunded",
                              ].includes(order.status) &&
                              ["paid", "partially_refunded"].includes(
                                order.payment_status,
                              ) &&
                              !order.cancel_requested_at;
                            if (!allowed) return null;
                            return (
                              <div
                                className="partial-cancel"
                                data-testid={`partial-cancel-${order.order_no}`}
                              >
                                <button
                                  className="btn btn-ghost btn-sm"
                                  type="button"
                                  data-testid={`partial-cancel-open-${order.order_no}`}
                                  onClick={() =>
                                    setPartialCancelFor(
                                      partialCancelFor === order.order_no
                                        ? null
                                        : order.order_no,
                                    )
                                  }
                                >
                                  {partialCancelFor === order.order_no
                                    ? "收起取消表单"
                                    : "取消未发货商品"}
                                </button>
                                {partialCancelFor === order.order_no ? (
                                  <div className="ship-form">
                                    <h4>选择取消数量</h4>
                                    {cancelableItems.map((item) => (
                                      <div
                                        className="field"
                                        key={`partial-${item.sku}`}
                                      >
                                        <label
                                          htmlFor={`partial-qty-${order.order_no}-${item.sku}`}
                                        >
                                          {item.sku}（可取消{" "}
                                          {item.cancelable_qty}）
                                        </label>
                                        <input
                                          id={`partial-qty-${order.order_no}-${item.sku}`}
                                          data-testid={`partial-cancel-qty-${order.order_no}-${item.sku}`}
                                          type="number"
                                          min={0}
                                          max={item.cancelable_qty}
                                          value={
                                            partialCancelQty[order.order_no]?.[
                                              item.sku
                                            ] ?? ""
                                          }
                                          onChange={(event) =>
                                            updatePartialCancelQty(
                                              order.order_no,
                                              item.sku,
                                              event.target.value,
                                            )
                                          }
                                        />
                                      </div>
                                    ))}
                                    <button
                                      className="btn btn-danger btn-sm"
                                      type="button"
                                      data-testid={`partial-cancel-submit-${order.order_no}`}
                                      disabled={busy}
                                      onClick={() =>
                                        submitPartialCancellation(
                                          order.order_no,
                                        )
                                      }
                                    >
                                      提交取消申请
                                    </button>
                                  </div>
                                ) : null}
                              </div>
                            );
                          })()}
                        </div>

                        {["delivered", "completed"].includes(order.status) ? (
                          <div
                            className="detail-box"
                            data-testid={`review-box-${order.order_no}`}
                          >
                            <h4>商品评价</h4>
                            {detail.items
                              .filter(
                                (item) => item.cancelled_qty < item.qty,
                              )
                              .map((item) => {
                              const key = `${order.order_no}:${item.sku}`;
                              const existing = reviewMap[key];
                              const form = reviewForms[key] ?? {
                                rating: "",
                                content: "",
                              };
                              return (
                                <div
                                  className="proposal-actions"
                                  key={item.sku}
                                  style={{ marginTop: 8, flexWrap: "wrap" }}
                                  data-testid={`review-row-${order.order_no}-${item.sku}`}
                                >
                                  <span style={{ minWidth: 180 }}>
                                    {item.title}
                                  </span>
                                  {existing ? (
                                    <>
                                      <span className="badge badge-ok">
                                        已评价 {existing.rating} 星
                                      </span>
                                      {existing.image_urls.length > 0 ? (
                                        <div className="review-images">
                                          {existing.image_urls.map((url) => (
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
                                              />
                                            </a>
                                          ))}
                                        </div>
                                      ) : null}
                                      {existing.merchant_reply ? (
                                        <span className="muted">
                                          商家回复：{existing.merchant_reply}
                                        </span>
                                      ) : null}
                                      {existing.follow_up_content ? (
                                        <div
                                          className="review-follow-up"
                                          data-testid={`review-follow-up-view-${order.order_no}-${item.sku}`}
                                        >
                                          <strong>追评</strong>
                                          <p>{existing.follow_up_content}</p>
                                          {existing.follow_up_image_urls
                                            .length > 0 ? (
                                            <div className="review-images">
                                              {existing.follow_up_image_urls.map(
                                                (url) => (
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
                                                    />
                                                  </a>
                                                ),
                                              )}
                                            </div>
                                          ) : null}
                                        </div>
                                      ) : existing.status === "visible" ? (
                                        <div className="review-follow-up-form">
                                          <input
                                            data-testid={`review-follow-up-${order.order_no}-${item.sku}`}
                                            value={
                                              followUpForms[key] ?? ""
                                            }
                                            onChange={(event) =>
                                              setFollowUpForms(
                                                (previous) => ({
                                                  ...previous,
                                                  [key]: event.target.value,
                                                }),
                                              )
                                            }
                                            placeholder="追评内容（1000 字以内）"
                                            maxLength={1000}
                                            style={{
                                              flex: 1,
                                              minWidth: 200,
                                            }}
                                          />
                                          <label
                                            className="btn btn-ghost btn-sm"
                                            style={{ cursor: "pointer" }}
                                          >
                                            图片（最多 3 张）
                                            <input
                                              type="file"
                                              hidden
                                              multiple
                                              accept="image/png,image/jpeg,image/webp"
                                              data-testid={`review-follow-up-files-${order.order_no}-${item.sku}`}
                                              onChange={(event) =>
                                                setFollowUpFiles(
                                                  (previous) => ({
                                                    ...previous,
                                                    [key]: Array.from(
                                                      event.target.files ??
                                                        [],
                                                    ).slice(0, 3),
                                                  }),
                                                )
                                              }
                                            />
                                          </label>
                                          <button
                                            className="btn btn-ghost btn-sm"
                                            type="button"
                                            data-testid={`submit-review-follow-up-${order.order_no}-${item.sku}`}
                                            disabled={
                                              followUpBusyKey === key ||
                                              !(followUpForms[key] ?? "").trim()
                                            }
                                            onClick={() =>
                                              void submitFollowUp(
                                                order.order_no,
                                                item.sku,
                                                existing.id,
                                              )
                                            }
                                          >
                                            {followUpBusyKey === key
                                              ? "提交中"
                                              : "提交追评"}
                                          </button>
                                        </div>
                                      ) : null}
                                    </>
                                  ) : (
                                    <>
                                      <select
                                        data-testid={`review-rating-${order.order_no}-${item.sku}`}
                                        value={form.rating}
                                        onChange={(event) =>
                                          setReviewForms((previous) => ({
                                            ...previous,
                                            [key]: {
                                              ...form,
                                              rating: event.target.value,
                                            },
                                          }))
                                        }
                                        aria-label={`${item.title} 评分`}
                                      >
                                        <option value="">评分</option>
                                        {[5, 4, 3, 2, 1].map((score) => (
                                          <option
                                            key={score}
                                            value={score}
                                          >
                                            {score} 星
                                          </option>
                                        ))}
                                      </select>
                                      <input
                                        data-testid={`review-content-${order.order_no}-${item.sku}`}
                                        value={form.content}
                                        onChange={(event) =>
                                          setReviewForms((previous) => ({
                                            ...previous,
                                            [key]: {
                                              ...form,
                                              content: event.target.value,
                                            },
                                          }))
                                        }
                                        placeholder="评价内容（可选）"
                                        maxLength={2000}
                                        style={{
                                          flex: 1,
                                          minWidth: 200,
                                        }}
                                      />
                                      <label
                                        className="btn btn-ghost btn-sm"
                                        style={{ cursor: "pointer" }}
                                      >
                                        图片（最多 3 张）
                                        <input
                                          type="file"
                                          hidden
                                          multiple
                                          accept="image/png,image/jpeg,image/webp"
                                          data-testid={`review-files-${order.order_no}-${item.sku}`}
                                          onChange={(event) => {
                                            const files = Array.from(
                                              event.target.files ?? [],
                                            );
                                            setReviewFiles((previous) => ({
                                              ...previous,
                                              [key]: files.slice(0, 3),
                                            }));
                                          }}
                                        />
                                      </label>
                                      <button
                                        className="btn btn-primary btn-sm"
                                        type="button"
                                        data-testid={`submit-review-${order.order_no}-${item.sku}`}
                                        disabled={
                                          !form.rating ||
                                          reviewBusyKey === key
                                        }
                                        onClick={() =>
                                          void submitReview(
                                            order.order_no,
                                            item.sku,
                                          )
                                        }
                                      >
                                        {reviewBusyKey === key
                                          ? "提交中"
                                          : "提交评价"}
                                      </button>
                                    </>
                                  )}
                                </div>
                              );
                            })}
                          </div>
                        ) : null}

                        {["paid", "partially_refunded"].includes(
                          order.payment_status,
                        ) ? (
                          <div
                            className="detail-box"
                            data-testid={`invoice-box-${order.order_no}`}
                          >
                            <h4>发票</h4>
                            {invoiceMap[order.order_no] ? (
                              <div
                                className="proposal-meta"
                                data-testid={`invoice-status-${order.order_no}`}
                              >
                                <span>
                                  {invoiceMap[order.order_no].invoice_type ===
                                  "company"
                                    ? "企业"
                                    : "个人"}{" "}
                                  · {invoiceMap[order.order_no].title}
                                </span>
                                <StatusBadge
                                  label={invoiceStatusLabel(
                                    invoiceMap[order.order_no].status,
                                  )}
                                  tone={
                                    invoiceMap[order.order_no].status ===
                                    "issued"
                                      ? "badge-ok"
                                      : invoiceMap[order.order_no].status ===
                                            "rejected" ||
                                          invoiceMap[order.order_no].status ===
                                            "voided"
                                        ? "badge-muted"
                                        : "badge-warn"
                                  }
                                />
                                {invoiceMap[order.order_no]
                                  .adjustment_required ? (
                                  <span className="badge badge-warn">
                                    退款后需重开
                                  </span>
                                ) : null}
                                <span>
                                  发票金额{" "}
                                  {(
                                    invoiceMap[order.order_no]
                                      .amount_cents / 100
                                  ).toFixed(2)}{" "}
                                  元 · 当前净支付{" "}
                                  {(
                                    invoiceMap[order.order_no]
                                      .net_paid_cents / 100
                                  ).toFixed(2)}{" "}
                                  元
                                </span>
                                {invoiceMap[order.order_no].invoice_no ? (
                                  <span>
                                    发票号{" "}
                                    {invoiceMap[order.order_no].invoice_no}
                                  </span>
                                ) : null}
                                {invoiceMap[order.order_no]
                                  .reject_reason ? (
                                  <span>
                                    {
                                      invoiceMap[order.order_no]
                                        .reject_reason
                                    }
                                  </span>
                                ) : null}
                                {invoiceMap[order.order_no].void_reason ? (
                                  <span>
                                    作废：{invoiceMap[order.order_no].void_reason}
                                  </span>
                                ) : null}
                              </div>
                            ) : null}
                            {(!invoiceMap[order.order_no] ||
                              invoiceMap[order.order_no].status === "voided" ||
                              invoiceMap[order.order_no].status ===
                                "rejected") &&
                            (invoiceOpenFor === order.order_no ? (
                              <div className="form-grid">
                                <div className="field">
                                  <label>发票类型</label>
                                  <select
                                    data-testid={`invoice-type-${order.order_no}`}
                                    value={
                                      invoiceFormFor(order.order_no)
                                        .invoice_type
                                    }
                                    onChange={(event) =>
                                      patchInvoiceForm(order.order_no, {
                                        invoice_type: event.target.value as
                                          | "personal"
                                          | "company",
                                      })
                                    }
                                  >
                                    <option value="personal">个人</option>
                                    <option value="company">企业</option>
                                  </select>
                                </div>
                                <div className="field">
                                  <label>发票抬头</label>
                                  <input
                                    data-testid={`invoice-title-${order.order_no}`}
                                    value={
                                      invoiceFormFor(order.order_no).title
                                    }
                                    onChange={(event) =>
                                      patchInvoiceForm(order.order_no, {
                                        title: event.target.value,
                                      })
                                    }
                                    maxLength={120}
                                  />
                                </div>
                                <div className="field">
                                  <label>税号</label>
                                  <input
                                    data-testid={`invoice-tax-${order.order_no}`}
                                    value={
                                      invoiceFormFor(order.order_no).tax_no
                                    }
                                    onChange={(event) =>
                                      patchInvoiceForm(order.order_no, {
                                        tax_no: event.target.value.toUpperCase(),
                                      })
                                    }
                                    maxLength={32}
                                    disabled={
                                      invoiceFormFor(order.order_no)
                                        .invoice_type !== "company"
                                    }
                                  />
                                </div>
                                <div className="field">
                                  <label>接收邮箱</label>
                                  <input
                                    data-testid={`invoice-email-${order.order_no}`}
                                    value={
                                      invoiceFormFor(order.order_no).email
                                    }
                                    onChange={(event) =>
                                      patchInvoiceForm(order.order_no, {
                                        email: event.target.value,
                                      })
                                    }
                                    maxLength={255}
                                  />
                                </div>
                                <div className="proposal-actions">
                                  <button
                                    className="btn btn-primary btn-sm"
                                    type="button"
                                    data-testid={`submit-invoice-${order.order_no}`}
                                    disabled={invoiceBusy}
                                    onClick={() =>
                                      void submitInvoice(order.order_no)
                                    }
                                  >
                                    {invoiceBusy ? "提交中" : "提交申请"}
                                  </button>
                                  <button
                                    className="btn btn-ghost btn-sm"
                                    type="button"
                                    onClick={() =>
                                      setInvoiceOpenFor(null)
                                    }
                                  >
                                    取消
                                  </button>
                                </div>
                              </div>
                            ) : (
                              <button
                                className="btn btn-ghost btn-sm"
                                type="button"
                                data-testid={`request-invoice-${order.order_no}`}
                                onClick={() => {
                                  patchInvoiceForm(order.order_no, {});
                                  setInvoiceOpenFor(order.order_no);
                                }}
                              >
                                {invoiceMap[order.order_no]
                                  ? "重新申请发票"
                                  : "申请发票"}
                              </button>
                            ))}{" "}
                          </div>
                        ) : null}

                        {detail.shipments.length > 0 ? (
                          <div
                            className="detail-box"
                            data-testid={`shipment-timeline-${order.order_no}`}
                          >
                            <h4>
                              物流轨迹
                              {detail.shipments.length > 1
                                ? `（${detail.shipments.length} 个包裹）`
                                : ""}
                            </h4>
                            {detail.shipments.map((shipment) => (
                              <div
                                className="shipment-package"
                                key={`customer-package-${shipment.id}`}
                                data-testid={`customer-package-${order.order_no}-${shipment.package_no}`}
                              >
                                <div className="proposal-meta">
                                  <strong>包裹 #{shipment.package_no}</strong>
                                  <span>
                                    {shipment.carrier} ·{" "}
                                    {shipment.tracking_no}
                                  </span>
                                  <span>
                                    {shipmentStatusLabel(shipment.status)}
                                  </span>
                                  <span>
                                    商品：
                                    {shipment.items.length > 0
                                      ? shipment.items
                                          .map(
                                            (item) =>
                                              `${item.sku} × ${item.qty}`,
                                          )
                                          .join("、")
                                      : "整单发货"}
                                  </span>
                                </div>
                                {shipment.events.length > 0 ? (
                                  <ol className="shipment-timeline">
                                    {shipment.events.map((event, index) => (
                                      <li
                                        key={`${event.happened_at}-${index}`}
                                      >
                                        <span className="timeline-node">
                                          {event.node}
                                        </span>
                                        <span className="timeline-desc">
                                          {event.description}
                                        </span>
                                        <time>
                                          {formatDateTime(event.happened_at)}
                                        </time>
                                      </li>
                                    ))}
                                  </ol>
                                ) : (
                                  <span className="muted">暂无轨迹。</span>
                                )}
                              </div>
                            ))}
                          </div>
                        ) : null}

                        <div className="detail-grid" style={{ marginTop: 12 }}>
                          <div className="detail-box">
                            <h4>支付与履约</h4>
                            <dl className="kv">
                              <dt>支付单</dt>
                              <dd>
                                {detail.payment
                                  ? `#${detail.payment.id} · ${detail.payment.provider === "mock" ? "沙箱通道" : detail.payment.provider} · ${paymentStatusLabel(detail.payment.status)} · ${detail.payment.amount_display} 元`
                                  : "—"}
                              </dd>
                              <dt>物流</dt>
                              <dd>
                                {detail.shipments.length > 0
                                  ? detail.shipments
                                      .map(
                                        (shipment) =>
                                          `包裹 #${shipment.package_no} · ${shipment.carrier} · ${shipment.tracking_no} · ${shipmentStatusLabel(shipment.status)}`,
                                      )
                                      .join("；")
                                  : "暂无物流信息"}
                              </dd>
                              <dt>最近节点</dt>
                              <dd>
                                {detail.shipments.length > 0
                                  ? detail.shipments
                                      .map(
                                        (shipment) =>
                                          `#${shipment.package_no} ${shipment.current_node ?? "—"}`,
                                      )
                                      .join("；")
                                  : "—"}
                              </dd>
                            </dl>
                          </div>
                          <div className="detail-box">
                            <h4>收货信息</h4>
                            <dl className="kv">
                              <dt>收货人</dt>
                              <dd>{detail.address.recipient_name}</dd>
                              <dt>手机号</dt>
                              <dd>{detail.address.recipient_phone}</dd>
                              <dt>地址</dt>
                              <dd>
                                {[
                                  detail.address.region,
                                  detail.address.city,
                                  detail.address.detail,
                                ]
                                  .filter(Boolean)
                                  .join(" ")}
                              </dd>
                            </dl>
                          </div>
                        </div>

                        {detail.refunds.length > 0 ? (
                          <div
                            className="detail-box"
                            style={{ marginTop: 12 }}
                            data-testid={`refund-ledger-${order.order_no}`}
                          >
                            <h4>退款记录</h4>
                            <div className="proposal-meta">
                              <span>
                                累计退款 {detail.refunded_total_display} 元
                              </span>
                              <span>
                                净支付 {detail.net_paid_display} 元
                              </span>
                            </div>
                            <table className="table">
                              <thead>
                                <tr>
                                  <th>类型</th>
                                  <th>金额</th>
                                  <th>状态</th>
                                  <th>渠道状态</th>
                                  <th>原因</th>
                                  <th>时间</th>
                                  <th>处理引用</th>
                                </tr>
                              </thead>
                              <tbody>
                                {detail.refunds.map((refund) => (
                                  <tr key={`refund-${refund.id}`}>
                                    <td>
                                      {refund.kind === "compensation"
                                        ? "补偿"
                                        : "退款"}
                                    </td>
                                    <td>{refund.amount_display} 元</td>
                                    <td>
                                      {refund.status === "executed"
                                        ? "已执行"
                                        : refund.status}
                                    </td>
                                    <td
                                      data-testid={`refund-settlement-${order.order_no}-${refund.id}`}
                                    >
                                      {refund.settlement_status === "succeeded"
                                        ? "渠道已到账"
                                        : refund.settlement_status === "failed"
                                          ? `渠道失败：${refund.settlement_failure_reason || "—"}`
                                          : "渠道处理中"}
                                    </td>
                                    <td>{refund.reason || "—"}</td>
                                    <td>
                                      {formatDateTime(
                                        refund.settled_at ??
                                          refund.executed_at ??
                                          refund.created_at,
                                      )}
                                    </td>
                                    <td>
                                      {refund.settlement_reference ||
                                        refund.provider_refund_id}
                                    </td>
                                  </tr>
                                ))}
                              </tbody>
                            </table>
                            <p className="muted">
                              退款到账时间以支付渠道为准；渠道失败时商家会重试；补偿不计入退款总额与净支付。
                            </p>
                          </div>
                        ) : null}
                      </>
                    )}
                  </div>
                ) : null}
              </article>
            );
          })}
        </div>
      )}
      {hasMore ? (
        <button
          className="btn btn-ghost"
          type="button"
          disabled={loadingMore}
          onClick={() => void loadOrders(true)}
        >
          {loadingMore ? "加载中" : "加载更多订单"}
        </button>
      ) : null}
    </>
  );
}
