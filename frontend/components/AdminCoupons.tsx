"use client";

import { useCallback, useEffect, useState, type FormEvent } from "react";

import { StatusBadge } from "@/components/StatusBadge";
import { api, errorMessage } from "@/lib/api";
import { formatDateTime } from "@/lib/status";
import type { Coupon } from "@/lib/types";

function today(): string {
  return new Date().toISOString().slice(0, 10);
}

function plusDays(days: number): string {
  return new Date(Date.now() + days * 86_400_000)
    .toISOString()
    .slice(0, 10);
}

function couponValue(coupon: Coupon): string {
  return coupon.discount_type === "fixed"
    ? `${(coupon.value / 100).toFixed(2)} 元`
    : `${coupon.value}%`;
}

function scopeLabel(coupon: Coupon): string {
  if (coupon.scope_type === "category") {
    return `类目：${coupon.scope_values.join("、")}`;
  }
  if (coupon.scope_type === "sku") {
    return `SKU：${coupon.scope_values.join("、")}`;
  }
  return "全场商品";
}

export function AdminCoupons({ role }: { role: "owner" | "agent" }) {
  const [items, setItems] = useState<Coupon[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [form, setForm] = useState({
    code: "",
    discount_type: "fixed",
    value: "10",
    min_spend: "0",
    starts_on: today(),
    ends_on: plusDays(30),
    max_uses: "100",
    per_customer_limit: "1",
    scope_type: "all",
    scope_values: "",
    claimable: false,
  });

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setItems(await api<Coupon[]>("/api/admin/coupons"));
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
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const value =
        form.discount_type === "fixed"
          ? Math.round(Number(form.value) * 100)
          : Number(form.value);
      const created = await api<Coupon>("/api/admin/coupons", {
        method: "POST",
        body: JSON.stringify({
          code: form.code,
          discount_type: form.discount_type,
          value,
          min_spend_cents: Math.round(Number(form.min_spend) * 100),
          starts_on: form.starts_on,
          ends_on: form.ends_on || null,
          max_uses: form.max_uses ? Number(form.max_uses) : null,
          per_customer_limit: Number(form.per_customer_limit),
          scope_type: form.scope_type,
          scope_values: form.scope_values
            .split(/[，,、\n]/)
            .map((value) => value.trim())
            .filter(Boolean),
          claimable: form.claimable,
        }),
      });
      setNotice(`优惠券 ${created.code} 已创建`);
      setForm({ ...form, code: "" });
      await load();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  const toggle = async (coupon: Coupon) => {
    setBusyId(coupon.id);
    setError("");
    setNotice("");
    try {
      const status = coupon.status === "active" ? "disabled" : "active";
      await api(`/api/admin/coupons/${coupon.id}`, {
        method: "PATCH",
        body: JSON.stringify({ status }),
      });
      setNotice(
        `优惠券 ${coupon.code} 已${status === "active" ? "启用" : "停用"}`,
      );
      await load();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusyId(null);
    }
  };

  return (
    <section className="admin-coupons">
      {role === "owner" ? (
        <form className="panel panel-pad" onSubmit={create}>
          <div className="section-head" style={{ marginBottom: 10 }}>
            <div>
              <h3 style={{ margin: 0 }}>新建优惠券</h3>
              <p>单笔订单最多使用一张；优惠只抵扣商品金额，不含运费。</p>
            </div>
          </div>
          <div className="form-grid">
            <div className="field">
              <label htmlFor="coupon-code">优惠码</label>
              <input
                id="coupon-code"
                data-testid="coupon-code-input"
                value={form.code}
                onChange={(event) =>
                  setForm({
                    ...form,
                    code: event.target.value.toUpperCase(),
                  })
                }
                maxLength={32}
                placeholder="如 WELCOME20"
                required
              />
            </div>
            <div className="field">
              <label htmlFor="coupon-type">类型</label>
              <select
                id="coupon-type"
                data-testid="coupon-type"
                value={form.discount_type}
                onChange={(event) =>
                  setForm({ ...form, discount_type: event.target.value })
                }
              >
                <option value="fixed">固定金额</option>
                <option value="percent">百分比</option>
              </select>
            </div>
            <div className="field">
              <label htmlFor="coupon-value">
                {form.discount_type === "fixed"
                  ? "优惠金额（元）"
                  : "优惠百分比（1–100）"}
              </label>
              <input
                id="coupon-value"
                data-testid="coupon-value"
                type="number"
                min="0.01"
                step={form.discount_type === "fixed" ? "0.01" : "1"}
                value={form.value}
                onChange={(event) =>
                  setForm({ ...form, value: event.target.value })
                }
                required
              />
            </div>
            <div className="field">
              <label htmlFor="coupon-min">最低消费（元）</label>
              <input
                id="coupon-min"
                data-testid="coupon-min"
                type="number"
                min="0"
                step="0.01"
                value={form.min_spend}
                onChange={(event) =>
                  setForm({ ...form, min_spend: event.target.value })
                }
              />
            </div>
            <div className="field">
              <label htmlFor="coupon-start">开始日期</label>
              <input
                id="coupon-start"
                data-testid="coupon-start"
                type="date"
                value={form.starts_on}
                onChange={(event) =>
                  setForm({ ...form, starts_on: event.target.value })
                }
                required
              />
            </div>
            <div className="field">
              <label htmlFor="coupon-end">结束日期</label>
              <input
                id="coupon-end"
                data-testid="coupon-end"
                type="date"
                value={form.ends_on}
                onChange={(event) =>
                  setForm({ ...form, ends_on: event.target.value })
                }
              />
            </div>
            <div className="field">
              <label htmlFor="coupon-max">总使用次数（留空不限）</label>
              <input
                id="coupon-max"
                data-testid="coupon-max"
                type="number"
                min="1"
                value={form.max_uses}
                onChange={(event) =>
                  setForm({ ...form, max_uses: event.target.value })
                }
              />
            </div>
            <div className="field">
              <label htmlFor="coupon-per-customer">每客限用</label>
              <input
                id="coupon-per-customer"
                data-testid="coupon-per-customer"
                type="number"
                min="1"
                value={form.per_customer_limit}
                onChange={(event) =>
                  setForm({
                    ...form,
                    per_customer_limit: event.target.value,
                  })
                }
              />
            </div>
            <div className="field">
              <label className="muted">
                <input
                  type="checkbox"
                  data-testid="coupon-claimable"
                  checked={form.claimable}
                  onChange={(event) =>
                    setForm({ ...form, claimable: event.target.checked })
                  }
                />{" "}
                放入领券中心，顾客可自助领取
              </label>
            </div>
            <div className="field">
              <label htmlFor="coupon-scope">适用范围</label>
              <select
                id="coupon-scope"
                data-testid="coupon-scope-type"
                value={form.scope_type}
                onChange={(event) =>
                  setForm({
                    ...form,
                    scope_type: event.target.value,
                    scope_values: "",
                  })
                }
              >
                <option value="all">全场商品</option>
                <option value="category">指定类目</option>
                <option value="sku">指定 SKU</option>
              </select>
            </div>
            {form.scope_type !== "all" ? (
              <div className="field">
                <label htmlFor="coupon-scope-values">
                  {form.scope_type === "category"
                    ? "类目（逗号/顿号分隔）"
                    : "SKU（逗号/顿号分隔）"}
                </label>
                <input
                  id="coupon-scope-values"
                  data-testid="coupon-scope-values"
                  value={form.scope_values}
                  onChange={(event) =>
                    setForm({
                      ...form,
                      scope_values: event.target.value,
                    })
                  }
                  placeholder={
                    form.scope_type === "category"
                      ? "如：耳机、充电器"
                      : "如：SKU-EAR-001、SKU-PWR-001"
                  }
                  required
                />
              </div>
            ) : null}
          </div>
          <div className="proposal-actions" style={{ marginTop: 12 }}>
            <button
              className="btn btn-primary"
              type="submit"
              data-testid="create-coupon"
              disabled={busy}
            >
              {busy ? "创建中" : "创建优惠券"}
            </button>
          </div>
        </form>
      ) : (
        <div className="notice notice-warn">
          客服账号只能查看优惠券；创建和停用需要店主权限。
        </div>
      )}

      {notice ? <div className="notice notice-success">{notice}</div> : null}
      {error ? <div className="notice notice-error">{error}</div> : null}

      {loading ? (
        <div className="loading">加载优惠券…</div>
      ) : items.length === 0 ? (
        <div className="empty-state">还没有优惠券。</div>
      ) : (
        <div className="admin-grid">
          {items.map((coupon) => (
            <article
              className="proposal-card"
              key={coupon.id}
              data-testid={`coupon-${coupon.id}`}
            >
              <div className="proposal-head">
                <div className="proposal-title">
                  <strong>{coupon.code}</strong>
                  <span className="badge badge-neutral">
                    {couponValue(coupon)}
                  </span>
                  <StatusBadge
                    label={coupon.status === "active" ? "启用" : "停用"}
                    tone={
                      coupon.status === "active"
                        ? "badge-ok"
                        : "badge-muted"
                    }
                  />
                </div>
                <span className="muted">
                  {formatDateTime(coupon.created_at)}
                </span>
              </div>
              <div className="proposal-meta">
                <span>
                  有效期 {coupon.starts_on} 至{" "}
                  {coupon.ends_on ?? "长期"}
                </span>
                <span>
                  已用 {coupon.used_count} /{" "}
                  {coupon.max_uses ?? "不限"}
                </span>
                <span>每客限用 {coupon.per_customer_limit}</span>
                {coupon.claimable ? (
                  <span data-testid={`coupon-claims-${coupon.id}`}>
                    领券中心 · 已领 {coupon.claim_count}
                  </span>
                ) : null}
                <span>{scopeLabel(coupon)}</span>
                <span>
                  门槛{" "}
                  {(coupon.min_spend_cents / 100).toFixed(2)} 元
                </span>
              </div>
              {role === "owner" ? (
                <div className="proposal-actions">
                  <button
                    className={
                      coupon.status === "active"
                        ? "btn btn-danger btn-sm"
                        : "btn btn-primary btn-sm"
                    }
                    type="button"
                    data-testid={`toggle-coupon-${coupon.id}`}
                    disabled={busyId === coupon.id}
                    onClick={() => void toggle(coupon)}
                  >
                    {coupon.status === "active" ? "停用" : "启用"}
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
