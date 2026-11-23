"use client";

import Link from "next/link";
import {
  useCallback,
  useEffect,
  useState,
  type ChangeEvent,
  type FormEvent,
} from "react";

import { useCustomer } from "@/components/CustomerProvider";
import { api, errorMessage } from "@/lib/api";
import type {
  Address,
  Cart,
  CheckoutResult,
  CustomerAddress,
  ShippingMethod,
} from "@/lib/types";

const EMPTY_FORM = {
  recipient_name: "",
  recipient_phone: "",
  region: "",
  city: "",
  detail: "",
  email: "",
};

export default function CartPage() {
  const { customer } = useCustomer();
  const [cart, setCart] = useState<Cart | null>(null);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [form, setForm] = useState(EMPTY_FORM);
  const [addresses, setAddresses] = useState<CustomerAddress[]>([]);
  const [selectedAddressId, setSelectedAddressId] = useState("");
  const [checkout, setCheckout] = useState<CheckoutResult | null>(null);
  const [couponCode, setCouponCode] = useState("");
  const [shippingMethods, setShippingMethods] = useState<ShippingMethod[]>([]);
  const [shippingCode, setShippingCode] = useState("");
  const [paid, setPaid] = useState(false);

  const loadCart = useCallback(async () => {
    if (!customer) {
      setCart(null);
      return;
    }
    setLoading(true);
    try {
      setCart(await api<Cart>("/api/store/cart"));
      setError("");
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setLoading(false);
    }
  }, [customer]);

  useEffect(() => {
    let cancelled = false;
    api<ShippingMethod[]>("/api/store/shipping-methods")
      .then((rows) => {
        if (cancelled) return;
        setShippingMethods(rows);
        setShippingCode((previous) =>
          rows.some((row) => row.code === previous)
            ? previous
            : rows[0]?.code ?? "",
        );
      })
      .catch(() => {
        if (!cancelled) setShippingMethods([]);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    void loadCart();
  }, [loadCart]);

  useEffect(() => {
    if (customer) {
      setForm((previous) => ({
        ...previous,
        recipient_phone: previous.recipient_phone || customer.phone || "",
        email: previous.email || customer.email || "",
      }));
    }
  }, [customer]);

  useEffect(() => {
    if (!customer) {
      setAddresses([]);
      setSelectedAddressId("");
      return;
    }
    let cancelled = false;
    Promise.all([
      api<CustomerAddress[]>("/api/store/addresses").catch(() => []),
      api<Address | null>("/api/store/addresses/last").catch(() => null),
    ]).then(([saved, last]) => {
      if (cancelled) return;
      setAddresses(saved);
      const preferred = saved.find((row) => row.is_default) ?? saved[0] ?? last;
      if (!preferred) return;
      setSelectedAddressId(
        saved.length > 0 && "id" in preferred ? String(preferred.id) : "",
      );
      setForm((previous) =>
        previous.recipient_name
          ? previous
          : {
              ...previous,
              recipient_name: preferred.recipient_name,
              recipient_phone:
                previous.recipient_phone || preferred.recipient_phone,
              region: preferred.region,
              city: preferred.city,
              detail: preferred.detail,
            },
      );
    });
    return () => {
      cancelled = true;
    };
  }, [customer]);

  const setQty = async (sku: string, qty: number) => {
    if (qty < 0) return;
    setBusy(true);
    try {
      await api(`/api/store/cart/items/${encodeURIComponent(sku)}`, {
        method: "PUT",
        body: JSON.stringify({ qty }),
      });
      await loadCart();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  const removeItem = async (sku: string) => {
    setBusy(true);
    try {
      await api(`/api/store/cart/items/${encodeURIComponent(sku)}`, {
        method: "DELETE",
      });
      await loadCart();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  const updateField =
    (key: keyof typeof form) => (event: ChangeEvent<HTMLInputElement>) => {
      setForm((previous) => ({ ...previous, [key]: event.target.value }));
    };

  const chooseAddress = (value: string) => {
    setSelectedAddressId(value);
    const address = addresses.find((row) => String(row.id) === value);
    if (!address) return;
    setForm((previous) => ({
      ...previous,
      recipient_name: address.recipient_name,
      recipient_phone: address.recipient_phone,
      region: address.region,
      city: address.city,
      detail: address.detail,
    }));
  };

  const submitCheckout = async (event: FormEvent) => {
    event.preventDefault();
    if (!cart || cart.items.length === 0) {
      setError("购物车为空");
      return;
    }
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const { email, ...address } = form;
      const result = await api<CheckoutResult>("/api/store/checkout", {
        method: "POST",
        headers: { "Idempotency-Key": crypto.randomUUID() },
        body: JSON.stringify({
          address,
          email: email.trim() || undefined,
          coupon_code: couponCode.trim() || undefined,
          shipping_method_code: shippingCode || undefined,
        }),
      });
      setCheckout(result);
      setPaid(false);
      setNotice("订单已创建，请完成沙箱支付。");
      await loadCart();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  const pay = async () => {
    if (!checkout) return;
    setBusy(true);
    setError("");
    try {
      await api(`/api/store/payments/${checkout.payment_id}/mock-confirm`, {
        method: "POST",
      });
      setPaid(true);
      setNotice("支付已确认，订单状态已更新。");
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  const selectedShipping =
    shippingMethods.find((method) => method.code === shippingCode) ?? null;
  const estimatedFreight = selectedShipping
    ? selectedShipping.free_over_cents !== null &&
      (cart?.item_total_cents ?? 0) >= selectedShipping.free_over_cents
      ? 0
      : selectedShipping.fee_cents
    : 0;
  const estimatedPayable = (cart?.item_total_cents ?? 0) + estimatedFreight;

  if (!customer) {
    return (
      <div className="empty-state">
        请先在右上角用手机号登录，再查看购物车。
      </div>
    );
  }

  return (
    <>
      <div className="section-head">
        <div>
          <h2>购物车</h2>
          <p>金额由服务端计算；提交订单时校验库存并创建支付单。</p>
        </div>
        <Link className="btn btn-ghost" href="/">
          继续购物
        </Link>
      </div>

      {notice ? <div className="notice notice-success">{notice}</div> : null}
      {error ? <div className="notice notice-error">{error}</div> : null}

      {checkout ? (
        <div className="panel panel-pad" style={{ marginBottom: 18 }}>
          <div className="section-head" style={{ marginBottom: 8 }}>
            <div>
              <h3 style={{ margin: 0 }}>订单已创建</h3>
              <p>
                订单号 {checkout.order_no}，应付 {checkout.amount_display} 元
                {checkout.discount_cents > 0
                  ? `（优惠 ${checkout.discount_display} 元${
                      checkout.coupon_code
                        ? `，优惠码 ${checkout.coupon_code}`
                        : ""
                    }）`
                  : ""}
                {checkout.freight_cents > 0
                  ? `，运费 ${checkout.freight_display} 元${
                      checkout.shipping_method_name
                        ? `（${checkout.shipping_method_name}）`
                        : ""
                    }`
                  : checkout.shipping_method_name
                    ? `，${checkout.shipping_method_name}免运费`
                    : ""}
              </p>
            </div>
            <span className={`badge ${paid ? "badge-ok" : "badge-warn"}`}>
              {paid ? "已支付" : "待支付"}
            </span>
          </div>
          <div className="card-actions">
            <button
              className="btn btn-primary"
              type="button"
              data-testid="mock-pay"
              disabled={busy || paid}
              onClick={() => void pay()}
            >
              {paid ? "支付已确认" : busy ? "处理中" : "沙箱支付"}
            </button>
            <Link className="btn btn-ghost" href="/orders">
              查看订单
            </Link>
          </div>
        </div>
      ) : null}

      {loading ? (
        <div className="loading">加载购物车…</div>
      ) : !cart || cart.items.length === 0 ? (
        <div className="empty-state">
          购物车还是空的。
          <div style={{ marginTop: 12 }}>
            <Link className="btn btn-primary" href="/">
              去选商品
            </Link>
          </div>
        </div>
      ) : (
        <div className="cart-layout">
          <section className="panel">
            <div className="panel-head">
              <h2>商品明细</h2>
              <span className="badge badge-neutral">{cart.items.length} 种商品</span>
            </div>
            {cart.items.map((item) => (
              <div className="cart-row" key={item.sku}>
                <div className="cart-row-main">
                  <strong>{item.title}</strong>
                  <span>
                    {item.sku} · 单价 {item.unit_price_display} 元 · 可售{" "}
                    {item.available_qty}
                  </span>
                </div>
                <div className="qty-control">
                  <button
                    type="button"
                    aria-label="减少数量"
                    disabled={busy}
                    onClick={() => void setQty(item.sku, item.qty - 1)}
                  >
                    −
                  </button>
                  <span>{item.qty}</span>
                  <button
                    type="button"
                    aria-label="增加数量"
                    disabled={busy || item.qty >= item.available_qty}
                    onClick={() => void setQty(item.sku, item.qty + 1)}
                  >
                    +
                  </button>
                  <button
                    className="btn btn-ghost btn-sm"
                    type="button"
                    disabled={busy}
                    onClick={() => void removeItem(item.sku)}
                  >
                    移除
                  </button>
                </div>
                <div className="cart-line-total">{item.line_total_display} 元</div>
              </div>
            ))}
          </section>

          <section className="panel summary">
            <h2 style={{ marginTop: 0, fontSize: 16 }}>结算</h2>
            <div className="summary-line">
              <span>商品小计</span>
              <span>{cart.item_total_display} 元</span>
            </div>
            <div className="summary-line">
              <span>运费</span>
              <span>{(estimatedFreight / 100).toFixed(2)} 元</span>
            </div>
            <div className="summary-line">
              <span>优惠</span>
              <span>{cart.discount_display} 元</span>
            </div>
            <div className="summary-line total">
              <span>应付（未含优惠码）</span>
              <span>{(estimatedPayable / 100).toFixed(2)} 元</span>
            </div>
            <p className="muted">
              优惠码在提交订单时校验，最终金额以订单创建结果为准。
            </p>

            <form onSubmit={submitCheckout} style={{ marginTop: 16 }}>
              {shippingMethods.length > 0 ? (
                <div className="field" style={{ marginBottom: 10 }}>
                  <label htmlFor="shipping_method">配送方式</label>
                  <select
                    id="shipping_method"
                    data-testid="shipping-method"
                    value={shippingCode}
                    onChange={(event) =>
                      setShippingCode(event.target.value)
                    }
                  >
                    {shippingMethods.map((method) => (
                      <option key={method.code} value={method.code}>
                        {method.name} ·{" "}
                        {(method.fee_cents / 100).toFixed(2)} 元
                        {method.free_over_cents !== null
                          ? `（满 ${(
                              method.free_over_cents / 100
                            ).toFixed(2)} 元包邮）`
                          : ""}
                      </option>
                    ))}
                  </select>
                </div>
              ) : null}
              {addresses.length > 0 ? (
                <div className="field" style={{ marginBottom: 10 }}>
                  <label htmlFor="saved_address">从地址簿选择</label>
                  <select
                    id="saved_address"
                    value={selectedAddressId}
                    onChange={(event) => chooseAddress(event.target.value)}
                    data-testid="address-select"
                  >
                    <option value="">手动填写</option>
                    {addresses.map((address) => (
                      <option key={address.id} value={address.id}>
                        {address.label ? `${address.label} · ` : ""}
                        {address.recipient_name} · {address.city}
                        {address.detail}
                      </option>
                    ))}
                  </select>
                  <Link
                    href="/addresses"
                    className="muted"
                    style={{ fontSize: 12 }}
                  >
                    管理地址簿
                  </Link>
                </div>
              ) : (
                <p className="muted" style={{ marginTop: 0 }}>
                  还没有保存的地址？
                  <Link href="/addresses">去地址簿添加</Link>
                </p>
              )}
              <div className="form-grid">
                <div className="field">
                  <label htmlFor="recipient_name">收货人</label>
                  <input
                    id="recipient_name"
                    value={form.recipient_name}
                    onChange={updateField("recipient_name")}
                    required
                    maxLength={120}
                  />
                </div>
                <div className="field">
                  <label htmlFor="recipient_phone">手机号</label>
                  <input
                    id="recipient_phone"
                    value={form.recipient_phone}
                    onChange={updateField("recipient_phone")}
                    required
                    maxLength={11}
                    inputMode="tel"
                  />
                </div>
                <div className="field">
                  <label htmlFor="region">区域</label>
                  <input
                    id="region"
                    value={form.region}
                    onChange={updateField("region")}
                    maxLength={80}
                    placeholder="如 华东"
                  />
                </div>
                <div className="field">
                  <label htmlFor="city">城市</label>
                  <input
                    id="city"
                    value={form.city}
                    onChange={updateField("city")}
                    required
                    maxLength={80}
                  />
                </div>
                <div className="field full">
                  <label htmlFor="detail">详细地址</label>
                  <input
                    id="detail"
                    value={form.detail}
                    onChange={updateField("detail")}
                    required
                    maxLength={255}
                  />
                </div>
                <div className="field full">
                  <label htmlFor="notify_email">通知邮箱（可选）</label>
                  <input
                    id="notify_email"
                    type="email"
                    value={form.email}
                    onChange={updateField("email")}
                    maxLength={255}
                    placeholder="用于接收发货与售后状态邮件"
                  />
                </div>
                <div className="field full">
                  <label htmlFor="coupon_code">优惠码（可选）</label>
                  <input
                    id="coupon_code"
                    data-testid="coupon-code"
                    value={couponCode}
                    onChange={(event) =>
                      setCouponCode(event.target.value.toUpperCase())
                    }
                    maxLength={32}
                    placeholder="如 WELCOME20，提交订单时校验"
                  />
                </div>
              </div>
              <button
                className="btn btn-primary btn-block"
                type="submit"
                data-testid="checkout-submit"
                disabled={busy}
                style={{ marginTop: 14 }}
              >
                {busy ? "提交中" : "提交订单"}
              </button>
            </form>
          </section>
        </div>
      )}
    </>
  );
}
