"use client";

import Link from "next/link";
import {
  useCallback,
  useEffect,
  useState,
  type FormEvent,
} from "react";

import { useCustomer } from "@/components/CustomerProvider";
import { api, errorMessage } from "@/lib/api";
import {
  actionTypeLabel,
  formatDateTime,
  orderStatusLabel,
  proposalStatusLabel,
  returnStatusLabel,
} from "@/lib/status";
import type {
  AfterSalesItem,
  CustomerAddress,
  OrderSummary,
  ReturnRequestView,
} from "@/lib/types";

export default function AccountPage() {
  const { customer, refresh } = useCustomer();
  const [name, setName] = useState("");
  const [addresses, setAddresses] = useState<CustomerAddress[]>([]);
  const [orders, setOrders] = useState<OrderSummary[]>([]);
  const [afterSales, setAfterSales] = useState<AfterSalesItem[]>([]);
  const [returns, setReturns] = useState<ReturnRequestView[]>([]);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    setName(customer?.name ?? "");
  }, [customer?.name]);

  const load = useCallback(async () => {
    if (!customer) {
      setAddresses([]);
      setOrders([]);
      setAfterSales([]);
      setReturns([]);
      return;
    }
    setLoading(true);
    try {
      const [addressRows, orderRows, afterSalesRows, returnRows] =
        await Promise.all([
          api<CustomerAddress[]>("/api/store/addresses"),
          api<OrderSummary[]>("/api/store/orders?limit=100"),
          api<AfterSalesItem[]>("/api/store/after-sales"),
          api<ReturnRequestView[]>("/api/store/returns"),
        ]);
      setAddresses(addressRows);
      setOrders(orderRows);
      setAfterSales(afterSalesRows);
      setReturns(returnRows);
      setError("");
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setLoading(false);
    }
  }, [customer]);

  useEffect(() => {
    void load();
  }, [load]);

  const saveName = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await api("/api/store/me", {
        method: "PATCH",
        body: JSON.stringify({ name: name.trim() }),
      });
      setNotice("称呼已保存");
      await refresh();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  if (!customer) {
    return (
      <div className="empty-state">
        请先在右上角用手机号登录，再查看账户。
      </div>
    );
  }

  return (
    <>
      <div className="section-head">
        <div>
          <h2>我的账户</h2>
          <p>
            {customer.phone ?? "—"} · {customer.email || "未设置通知邮箱"}
          </p>
        </div>
        <Link className="btn btn-ghost" href="/orders">
          我的订单
        </Link>
      </div>

      {notice ? <div className="notice notice-success">{notice}</div> : null}
      {error ? <div className="notice notice-error">{error}</div> : null}

      <div className="cart-layout">
        <section className="panel panel-pad">
          <div className="section-head" style={{ marginBottom: 10 }}>
            <div>
              <h3 style={{ margin: 0 }}>个人资料</h3>
              <p>称呼用于订单与售后沟通；手机号是登录凭据。</p>
            </div>
          </div>
          <form onSubmit={saveName}>
            <div className="form-grid">
              <div className="field full">
                <label htmlFor="account-name">称呼</label>
                <input
                  id="account-name"
                  value={name}
                  onChange={(event) => setName(event.target.value)}
                  maxLength={120}
                  data-testid="account-name"
                />
              </div>
              <div className="field">
                <label>手机号</label>
                <input value={customer.phone ?? ""} readOnly />
              </div>
              <div className="field">
                <label>通知邮箱</label>
                <input value={customer.email ?? ""} readOnly />
              </div>
            </div>
            <div className="proposal-actions" style={{ marginTop: 12 }}>
              <button
                className="btn btn-primary"
                type="submit"
                disabled={busy}
                data-testid="account-save"
              >
                {busy ? "保存中" : "保存称呼"}
              </button>
              <Link className="btn btn-ghost" href="/after-sales">
                管理通知邮箱
              </Link>
            </div>
          </form>

          <div className="overview-grid" style={{ marginTop: 18 }}>
            <div className="overview-card">
              <span className="overview-label">订单</span>
              <strong>{orders.length}</strong>
              <small>最近 100 单</small>
            </div>
            <div className="overview-card">
              <span className="overview-label">售后申请</span>
              <strong>{afterSales.length}</strong>
              <small>退款 / 补偿 / 改地址</small>
            </div>
            <div className="overview-card">
              <span className="overview-label">退货</span>
              <strong>{returns.length}</strong>
              <small>退货 / 换货</small>
            </div>
            <div className="overview-card">
              <span className="overview-label">地址</span>
              <strong>{addresses.length}</strong>
              <small>最多 10 个</small>
            </div>
          </div>
        </section>

        <section className="panel panel-pad">
          <div className="section-head" style={{ marginBottom: 10 }}>
            <div>
              <h3 style={{ margin: 0 }}>地址簿</h3>
              <p>默认地址优先用于结算预填。</p>
            </div>
            <Link className="btn btn-ghost btn-sm" href="/addresses">
              管理
            </Link>
          </div>
          {addresses.length === 0 ? (
            <div className="empty-state">还没有保存地址。</div>
          ) : (
            <div className="admin-grid">
              {addresses.slice(0, 3).map((address) => (
                <article className="proposal-card" key={address.id}>
                  <div className="proposal-head">
                    <div className="proposal-title">
                      <strong>{address.label || address.recipient_name}</strong>
                      {address.is_default ? (
                        <span className="badge badge-ok">默认</span>
                      ) : null}
                    </div>
                  </div>
                  <div className="proposal-meta">
                    <span>{address.recipient_phone}</span>
                    <span>
                      {address.region} {address.city}
                    </span>
                  </div>
                  <p className="after-sales-reason">{address.detail}</p>
                </article>
              ))}
            </div>
          )}
        </section>
      </div>

      <section className="after-sales-section" style={{ marginTop: 18 }}>
        <div className="section-head" style={{ marginBottom: 10 }}>
          <div>
            <h3 style={{ margin: 0 }}>最近订单</h3>
            <p>完整的筛选与搜索在「我的订单」。</p>
          </div>
          <Link className="btn btn-ghost btn-sm" href="/orders">
            全部订单
          </Link>
        </div>
        {loading ? (
          <div className="loading">加载账户数据…</div>
        ) : orders.length === 0 ? (
          <div className="empty-state">还没有订单。</div>
        ) : (
          <div className="order-list">
            {orders.slice(0, 5).map((order) => (
              <div className="proposal-card" key={order.order_no}>
                <div className="proposal-head">
                  <div className="proposal-title">
                    <strong>{order.order_no}</strong>
                    <span className="badge badge-neutral">
                      {orderStatusLabel(order.status)}
                    </span>
                  </div>
                  <span className="muted">
                    {formatDateTime(order.placed_at)}
                  </span>
                </div>
                <div className="proposal-meta">
                  <span>{order.item_count} 件</span>
                  <span>{order.payable_display} 元</span>
                </div>
              </div>
            ))}
          </div>
        )}
      </section>

      {afterSales.length > 0 || returns.length > 0 ? (
        <section className="after-sales-section" style={{ marginTop: 18 }}>
          <h3>最近售后</h3>
          <div className="proposal-meta">
            {afterSales.slice(0, 3).map((item) => (
              <span key={`after-sales-${item.id}`}>
                提案 #{item.id} {actionTypeLabel(item.action_type)} ·{" "}
                {proposalStatusLabel(item.status)}
              </span>
            ))}
            {returns.slice(0, 3).map((item) => (
              <span key={`return-${item.id}`}>
                退货 #{item.id} ·{" "}
                {item.resolution === "exchange" ? "换货" : "退货退款"} ·{" "}
                {returnStatusLabel(item.status)}
              </span>
            ))}
          </div>
          <Link className="btn btn-ghost btn-sm" href="/after-sales">
            查看售后进度
          </Link>
        </section>
      ) : null}
    </>
  );
}
