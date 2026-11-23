"use client";

import { useCallback, useEffect, useState } from "react";

import { StatusBadge } from "@/components/StatusBadge";
import { api, errorMessage } from "@/lib/api";
import {
  formatDateTime,
  fulfillmentStatusLabel,
  orderStatusLabel,
  paymentStatusLabel,
  shipmentStatusLabel,
} from "@/lib/status";
import type { AdminOrder, AdminOrderDetail } from "@/lib/types";

type NextStatus = "in_transit" | "out_for_delivery" | "delivered" | "exception";

const NEXT_ACTIONS: Record<string, { value: NextStatus; label: string }[]> = {
  pending: [
    { value: "in_transit", label: "承运商已揽收" },
    { value: "exception", label: "登记异常" },
  ],
  in_transit: [
    { value: "out_for_delivery", label: "到达派送网点" },
    { value: "exception", label: "登记异常" },
  ],
  out_for_delivery: [
    { value: "delivered", label: "确认签收" },
    { value: "exception", label: "登记异常" },
  ],
  exception: [
    { value: "in_transit", label: "恢复运输" },
    { value: "out_for_delivery", label: "重新派送" },
    { value: "delivered", label: "确认签收" },
  ],
  delivered: [],
};

type AdvanceForm = {
  status: NextStatus;
  node: string;
  description: string;
};

type ShipForm = {
  carrier: string;
  tracking_no: string;
  quantities: Record<string, string>;
};

export function AdminFulfillment({ role }: { role: "owner" | "agent" }) {
  const [orders, setOrders] = useState<AdminOrder[]>([]);
  const [openOnly, setOpenOnly] = useState(true);
  const [searchInput, setSearchInput] = useState("");
  const [search, setSearch] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [shipForms, setShipForms] = useState<Record<string, ShipForm>>({});
  const [advanceForms, setAdvanceForms] = useState<Record<string, AdvanceForm>>(
    {},
  );
  const [detail, setDetail] = useState<AdminOrderDetail | null>(null);
  const [detailBusy, setDetailBusy] = useState(false);

  const loadOrders = useCallback(async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams({
        open_only: openOnly ? "true" : "false",
        limit: "50",
      });
      if (search.trim()) params.set("q", search.trim());
      const rows = await api<AdminOrder[]>(
        `/api/admin/orders?${params.toString()}`,
      );
      setOrders(rows);
      setError("");
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setLoading(false);
    }
  }, [openOnly, search]);

  useEffect(() => {
    void loadOrders();
  }, [loadOrders]);

  const openDetail = async (orderNo: string) => {
    setDetailBusy(true);
    setError("");
    try {
      setDetail(
        await api<AdminOrderDetail>(
          `/api/admin/orders/${encodeURIComponent(orderNo)}`,
        ),
      );
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setDetailBusy(false);
    }
  };

  const defaultShipForm = (order: AdminOrder): ShipForm => ({
    carrier: "顺丰速运",
    tracking_no: "",
    quantities: Object.fromEntries(
      order.items
        .filter((item) => item.remaining_qty > 0)
        .map((item) => [String(item.order_item_id), String(item.remaining_qty)]),
    ),
  });

  const shipForm = (order: AdminOrder) =>
    shipForms[order.order_no] ?? defaultShipForm(order);

  const advanceKey = (orderNo: string, shipmentId: number) =>
    `${orderNo}:${shipmentId}`;

  const advanceForm = (
    order: AdminOrder,
    shipmentId: number,
    status: string,
  ): AdvanceForm => {
    const current = advanceForms[advanceKey(order.order_no, shipmentId)];
    if (current) return current;
    const options = NEXT_ACTIONS[status] ?? [];
    return {
      status: options[0]?.value ?? "in_transit",
      node: "",
      description: "",
    };
  };

  const updateShipForm = (
    order: AdminOrder,
    field: "carrier" | "tracking_no",
    value: string,
  ) => {
    setShipForms((previous) => ({
      ...previous,
      [order.order_no]: { ...shipForm(order), [field]: value },
    }));
  };

  const updateShipQty = (
    order: AdminOrder,
    orderItemId: number,
    value: string,
  ) => {
    const form = shipForm(order);
    setShipForms((previous) => ({
      ...previous,
      [order.order_no]: {
        ...form,
        quantities: {
          ...form.quantities,
          [String(orderItemId)]: value,
        },
      },
    }));
  };

  const updateAdvanceForm = (
    order: AdminOrder,
    shipmentId: number,
    status: string,
    patch: Partial<AdvanceForm>,
  ) => {
    setAdvanceForms((previous) => ({
      ...previous,
      [advanceKey(order.order_no, shipmentId)]: {
        ...advanceForm(order, shipmentId, status),
        ...patch,
      },
    }));
  };

  const createShipment = async (order: AdminOrder) => {
    const orderNo = order.order_no;
    const form = shipForm(order);
    const items = order.items
      .filter((item) => item.remaining_qty > 0)
      .map((item) => ({
        order_item_id: item.order_item_id,
        qty: Number(form.quantities[String(item.order_item_id)] ?? 0),
      }))
      .filter((item) => Number.isFinite(item.qty) && item.qty > 0);
    if (items.length === 0) {
      setError("请填写至少一个商品的发货数量");
      return;
    }
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const result = await api<{ package_no: number }>(
        `/api/admin/orders/${orderNo}/shipment`,
        {
          method: "POST",
          body: JSON.stringify({
            carrier: form.carrier,
            tracking_no: form.tracking_no,
            items,
          }),
        },
      );
      setNotice(
        `订单 ${orderNo} 已创建运单（包裹 #${result.package_no}），等待揽收`,
      );
      setShipForms((previous) => {
        const next = { ...previous };
        delete next[orderNo];
        return next;
      });
      await loadOrders();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  const advanceShipment = async (
    order: AdminOrder,
    shipmentId: number,
    status: string,
  ) => {
    const form = advanceForm(order, shipmentId, status);
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await api(`/api/admin/orders/${order.order_no}/shipment/advance`, {
        method: "POST",
        body: JSON.stringify({ ...form, shipment_id: shipmentId }),
      });
      setNotice(
        `订单 ${order.order_no} 物流已更新为${shipmentStatusLabel(form.status)}`,
      );
      setAdvanceForms((previous) => {
        const next = { ...previous };
        delete next[advanceKey(order.order_no, shipmentId)];
        return next;
      });
      await loadOrders();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="admin-fulfillment">
      <div className="fulfillment-toolbar">
        <div className="segmented" role="group" aria-label="履约订单范围">
          <button
            className={`btn btn-sm ${openOnly ? "btn-primary" : "btn-ghost"}`}
            type="button"
            onClick={() => setOpenOnly(true)}
          >
            待履约
          </button>
          <button
            className={`btn btn-sm ${openOnly ? "btn-ghost" : "btn-primary"}`}
            type="button"
            onClick={() => setOpenOnly(false)}
          >
            全部订单
          </button>
        </div>
        <form
          className="proposal-search"
          onSubmit={(event) => {
            event.preventDefault();
            setSearch(searchInput);
          }}
        >
          <input
            value={searchInput}
            onChange={(event) => setSearchInput(event.target.value)}
            placeholder="搜索订单号/收货人/手机号"
            maxLength={100}
            aria-label="搜索履约订单"
            data-testid="fulfillment-search"
          />
          <button className="btn btn-ghost btn-sm" type="submit">
            搜索
          </button>
        </form>
        <button
          className="btn btn-ghost btn-sm"
          type="button"
          onClick={() => void loadOrders()}
        >
          刷新
        </button>
      </div>

      {role !== "owner" ? (
        <div className="notice notice-warn">
          客服账号只能查看履约状态；创建运单和推进物流节点需要店主权限。
        </div>
      ) : null}
      {notice ? <div className="notice notice-success">{notice}</div> : null}
      {error ? <div className="notice notice-error">{error}</div> : null}

      {detail ? (
        <section
          className="panel panel-pad"
          style={{ marginBottom: 16 }}
          data-testid="admin-order-detail"
        >
          <div className="section-head" style={{ marginBottom: 10 }}>
            <div>
              <h3 style={{ margin: 0 }}>订单 {detail.order_no}</h3>
              <p>
                顾客 {detail.customer_name ?? "未命名"} ·{" "}
                {detail.customer_phone ?? "—"} ·{" "}
                {detail.recipient_name} · {detail.recipient_phone}
              </p>
            </div>
            <button
              className="btn btn-ghost btn-sm"
              type="button"
              data-testid="admin-order-detail-close"
              onClick={() => setDetail(null)}
            >
              关闭详情
            </button>
          </div>
          <div className="proposal-meta">
            <StatusBadge
              label={orderStatusLabel(detail.status)}
              status={detail.status}
            />
            <StatusBadge
              label={paymentStatusLabel(detail.payment_status)}
              status={detail.payment_status}
            />
            <StatusBadge
              label={fulfillmentStatusLabel(detail.fulfillment_status)}
              status={detail.fulfillment_status}
            />
            <span>
              收货 {detail.region} {detail.city} {detail.detail}
            </span>
            <span>应付 {detail.payable_display} 元</span>
          </div>

          <h4>商品</h4>
          <table className="table">
            <thead>
              <tr>
                <th>SKU</th>
                <th>标题</th>
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
                    {item.cancelled_qty > 0 ? item.cancelled_qty : "—"}
                  </td>
                  <td>{item.unit_price_display}</td>
                  <td>{item.line_total_display}</td>
                </tr>
              ))}
            </tbody>
          </table>

          <h4>支付与退款</h4>
          <div className="proposal-meta">
            {detail.payments.map((payment) => (
              <span key={`payment-${payment.id}`}>
                支付 #{payment.id} · {payment.provider} ·{" "}
                {payment.amount_display} 元 · {payment.status}
                {payment.paid_at
                  ? ` · ${formatDateTime(payment.paid_at)}`
                  : ""}
              </span>
            ))}
            {detail.refunds.map((refund) => (
              <span key={`refund-${refund.id}`}>
                {refund.kind === "compensation" ? "补偿" : "退款"} #
                {refund.id} · {refund.amount_display} 元 · {refund.status} ·
                提案 #{refund.proposal_id}
              </span>
            ))}
            {detail.payments.length === 0 && detail.refunds.length === 0 ? (
              <span className="muted">暂无支付/退款记录。</span>
            ) : null}
          </div>

          <h4>物流</h4>
          {detail.shipments.length > 0 ? (
            detail.shipments.map((shipment) => (
              <div
                className="shipment-package"
                key={`detail-package-${shipment.id}`}
                data-testid={`admin-detail-package-${shipment.package_no}`}
              >
                <div className="proposal-meta">
                  <strong>包裹 #{shipment.package_no}</strong>
                  <span>
                    {shipment.carrier} · {shipment.tracking_no}
                  </span>
                  <StatusBadge
                    label={shipmentStatusLabel(shipment.status)}
                    status={shipment.status}
                  />
                  <span>当前节点：{shipment.current_node ?? "—"}</span>
                  <span>
                    商品：
                    {shipment.items.length > 0
                      ? shipment.items
                          .map((item) => `${item.sku} × ${item.qty}`)
                          .join("、")
                      : "整单发货"}
                  </span>
                </div>
                <ol className="shipment-timeline">
                  {[...shipment.events].reverse().map((event, index) => (
                    <li key={`${event.happened_at}-${index}`}>
                      <span className="timeline-node">{event.node}</span>
                      <span className="timeline-desc">{event.description}</span>
                      <time>{formatDateTime(event.happened_at)}</time>
                    </li>
                  ))}
                </ol>
              </div>
            ))
          ) : (
            <span className="muted">暂无运单。</span>
          )}

          <h4>售后提案与退货</h4>
          <div className="proposal-meta">
            {detail.proposals.map((proposal) => (
              <span key={`proposal-${proposal.id}`}>
                提案 #{proposal.id} · {proposal.action_type} · {proposal.status}
                {proposal.amount_display
                  ? ` · ${proposal.amount_display} 元`
                  : ""}
                {` · ${proposal.created_by}`}
              </span>
            ))}
            {detail.returns.map((request) => (
              <span key={`return-${request.id}`}>
                退货 #{request.id} ·{" "}
                {request.resolution === "exchange" ? "换货" : "退货退款"} ·{" "}
                {request.status}
                {request.exchange_sku ? ` · ${request.exchange_sku}` : ""}
              </span>
            ))}
            {detail.proposals.length === 0 && detail.returns.length === 0 ? (
              <span className="muted">暂无售后记录。</span>
            ) : null}
          </div>
        </section>
      ) : null}

      {loading ? (
        <div className="loading">加载履约订单…</div>
      ) : orders.length === 0 ? (
        <div className="empty-state">
          {openOnly ? "没有待履约订单。" : "没有可显示的订单。"}
        </div>
      ) : (
        <div className="admin-grid">
          {orders.map((order) => {
            const remainingItems = order.items.filter(
              (item) => item.remaining_qty > 0,
            );
            const statusLabel = orderStatusLabel(order.status);
            const paymentLabel = paymentStatusLabel(order.payment_status);
            const fulfillmentLabel = fulfillmentStatusLabel(
              order.fulfillment_status,
            );
            const multiple = order.shipments.length > 1;
            return (
              <article
                className="fulfillment-card"
                key={order.order_no}
                data-testid={`fulfillment-order-${order.order_no}`}
              >
                <div className="proposal-head">
                  <div className="order-title">
                    <strong>{order.order_no}</strong>
                    <StatusBadge label={statusLabel} status={order.status} />
                    {paymentLabel !== statusLabel ? (
                      <StatusBadge
                        label={paymentLabel}
                        status={order.payment_status}
                      />
                    ) : null}
                    {fulfillmentLabel !== statusLabel ? (
                      <StatusBadge
                        label={fulfillmentLabel}
                        status={order.fulfillment_status}
                      />
                    ) : null}
                  </div>
                  <span className="muted">
                    {formatDateTime(order.placed_at)}
                  </span>
                </div>

                <div className="proposal-meta">
                  <span>
                    收货 {order.recipient_name} · {order.recipient_phone}
                  </span>
                  <span>{order.address}</span>
                  <span>{order.item_count} 件商品</span>
                  <span>
                    {order.shipments.length} 个包裹
                    {remainingItems.length > 0 ? " · 仍有商品未发货" : ""}
                  </span>
                  <span>应付 {order.payable_display} 元</span>
                </div>

                <div className="proposal-meta">
                  {order.items.map((item) => (
                    <span key={`item-${item.order_item_id}`}>
                      {item.sku}：已发 {item.shipped_qty}/{item.qty}
                      {item.cancelled_qty > 0
                        ? `，已取消 ${item.cancelled_qty}`
                        : ""}
                      ，剩余 {item.remaining_qty}
                    </span>
                  ))}
                </div>

                <div className="proposal-actions">
                  <button
                    className="btn btn-ghost btn-sm"
                    type="button"
                    data-testid={`detail-${order.order_no}`}
                    disabled={detailBusy}
                    onClick={() => void openDetail(order.order_no)}
                  >
                    查看详情
                  </button>
                </div>

                {order.shipments.map((shipment) => {
                  const options = NEXT_ACTIONS[shipment.status] ?? [];
                  const form = advanceForm(
                    order,
                    shipment.id,
                    shipment.status,
                  );
                  const suffix = multiple ? `-${shipment.package_no}` : "";
                  return (
                    <div
                      className="shipment-package"
                      key={`package-${shipment.id}`}
                      data-testid={`fulfillment-package-${order.order_no}-${shipment.package_no}`}
                    >
                      <div className="shipment-summary">
                        <strong>包裹 #{shipment.package_no}</strong>
                        <span>
                          {shipment.carrier} · {shipment.tracking_no}
                        </span>
                        <StatusBadge
                          label={shipmentStatusLabel(shipment.status)}
                          status={shipment.status}
                        />
                        <span className="muted">
                          当前节点：{shipment.current_node ?? "—"}
                        </span>
                      </div>
                      <div className="proposal-meta">
                        <span>
                          商品：
                          {shipment.items.length > 0
                            ? shipment.items
                                .map((item) => `${item.sku} × ${item.qty}`)
                                .join("、")
                            : "整单发货"}
                        </span>
                      </div>

                      <ol
                        className="shipment-timeline"
                        data-testid={`admin-timeline-${order.order_no}${suffix}`}
                      >
                        {[...shipment.events].reverse().map((event, index) => (
                          <li key={`${event.happened_at}-${index}`}>
                            <span className="timeline-node">{event.node}</span>
                            <span className="timeline-desc">
                              {event.description}
                            </span>
                            <time>{formatDateTime(event.happened_at)}</time>
                          </li>
                        ))}
                      </ol>

                      {role === "owner" && options.length > 0 ? (
                        <div className="ship-form">
                          <div className="field">
                            <label
                              htmlFor={`next-status-${order.order_no}${suffix}`}
                            >
                              下一节点
                            </label>
                            <select
                              id={`next-status-${order.order_no}${suffix}`}
                              data-testid={`next-status-${order.order_no}${suffix}`}
                              value={form.status}
                              onChange={(event) =>
                                updateAdvanceForm(
                                  order,
                                  shipment.id,
                                  shipment.status,
                                  {
                                    status: event.target
                                      .value as NextStatus,
                                  },
                                )
                              }
                            >
                              {options.map((option) => (
                                <option key={option.value} value={option.value}>
                                  {option.label}
                                </option>
                              ))}
                            </select>
                          </div>
                          <div className="field">
                            <label htmlFor={`node-${order.order_no}${suffix}`}>
                              节点名称（可选）
                            </label>
                            <input
                              id={`node-${order.order_no}${suffix}`}
                              data-testid={`node-${order.order_no}${suffix}`}
                              value={form.node}
                              maxLength={64}
                              placeholder="如：上海徐汇营业点"
                              onChange={(event) =>
                                updateAdvanceForm(
                                  order,
                                  shipment.id,
                                  shipment.status,
                                  { node: event.target.value },
                                )
                              }
                            />
                          </div>
                          <div className="field">
                            <label
                              htmlFor={`description-${order.order_no}${suffix}`}
                            >
                              说明（可选）
                            </label>
                            <input
                              id={`description-${order.order_no}${suffix}`}
                              data-testid={`description-${order.order_no}${suffix}`}
                              value={form.description}
                              maxLength={500}
                              placeholder="如：快递员正在派送"
                              onChange={(event) =>
                                updateAdvanceForm(
                                  order,
                                  shipment.id,
                                  shipment.status,
                                  { description: event.target.value },
                                )
                              }
                            />
                          </div>
                          <button
                            className="btn btn-primary"
                            type="button"
                            data-testid={`advance-shipment-${order.order_no}${suffix}`}
                            disabled={busy}
                            onClick={() =>
                              void advanceShipment(
                                order,
                                shipment.id,
                                shipment.status,
                              )
                            }
                          >
                            更新物流
                          </button>
                        </div>
                      ) : null}

                      {shipment.status === "delivered" ? (
                        <p className="muted">
                          包裹 #{shipment.package_no} 已签收
                          {order.status === "delivered"
                            ? "，全部包裹签收，履约完成。"
                            : "，等待其余包裹。"}
                        </p>
                      ) : null}
                    </div>
                  );
                })}

                {remainingItems.length > 0 ? (
                  <div className="shipment-empty">
                    {role === "owner" ? (
                      <div className="ship-form">
                        <h4>
                          {order.shipments.length > 0 ? "新增包裹" : "创建运单"}
                        </h4>
                        <div className="field">
                          <label htmlFor={`carrier-${order.order_no}`}>
                            承运商
                          </label>
                          <input
                            id={`carrier-${order.order_no}`}
                            data-testid={`carrier-${order.order_no}`}
                            value={shipForm(order).carrier}
                            maxLength={64}
                            onChange={(event) =>
                              updateShipForm(
                                order,
                                "carrier",
                                event.target.value,
                              )
                            }
                          />
                        </div>
                        <div className="field">
                          <label htmlFor={`tracking-${order.order_no}`}>
                            运单号
                          </label>
                          <input
                            id={`tracking-${order.order_no}`}
                            data-testid={`tracking-${order.order_no}`}
                            value={shipForm(order).tracking_no}
                            maxLength={64}
                            placeholder="输入承运商运单号"
                            onChange={(event) =>
                              updateShipForm(
                                order,
                                "tracking_no",
                                event.target.value,
                              )
                            }
                          />
                        </div>
                        {remainingItems.map((item) => (
                          <div className="field" key={`qty-${item.order_item_id}`}>
                            <label
                              htmlFor={`ship-qty-${order.order_no}-${item.order_item_id}`}
                            >
                              {item.sku} 数量（剩余 {item.remaining_qty}）
                            </label>
                            <input
                              id={`ship-qty-${order.order_no}-${item.order_item_id}`}
                              data-testid={`ship-qty-${order.order_no}-${item.order_item_id}`}
                              type="number"
                              min={0}
                              max={item.remaining_qty}
                              value={
                                shipForm(order).quantities[
                                  String(item.order_item_id)
                                ] ?? ""
                              }
                              onChange={(event) =>
                                updateShipQty(
                                  order,
                                  item.order_item_id,
                                  event.target.value,
                                )
                              }
                            />
                          </div>
                        ))}
                        <button
                          className="btn btn-primary"
                          type="button"
                          data-testid={`create-shipment-${order.order_no}`}
                          disabled={
                            busy || !shipForm(order).tracking_no.trim()
                          }
                          onClick={() => void createShipment(order)}
                        >
                          创建包裹
                        </button>
                      </div>
                    ) : (
                      <span className="muted">仍有商品未发货。</span>
                    )}
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
