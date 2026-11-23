"use client";

import { useCallback, useEffect, useState, type FormEvent } from "react";

import { StatusBadge } from "@/components/StatusBadge";
import { api, errorMessage } from "@/lib/api";
import { formatDateTime } from "@/lib/status";
import type { ShippingMethod } from "@/lib/types";

export function AdminShipping({ role }: { role: "owner" | "agent" }) {
  const [items, setItems] = useState<ShippingMethod[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [form, setForm] = useState({
    code: "",
    name: "",
    fee: "0",
    free_over: "",
    sort_order: "0",
  });

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setItems(await api<ShippingMethod[]>("/api/admin/shipping-methods"));
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
      const created = await api<ShippingMethod>(
        "/api/admin/shipping-methods",
        {
          method: "POST",
          body: JSON.stringify({
            code: form.code,
            name: form.name,
            fee_cents: Math.round(Number(form.fee) * 100),
            free_over_cents: form.free_over
              ? Math.round(Number(form.free_over) * 100)
              : null,
            sort_order: Number(form.sort_order),
          }),
        },
      );
      setNotice(`配送方式 ${created.name} 已创建`);
      setForm({ ...form, code: "", name: "" });
      await load();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  const toggle = async (method: ShippingMethod) => {
    setBusyId(method.id);
    setError("");
    setNotice("");
    try {
      const status = method.status === "active" ? "disabled" : "active";
      await api(`/api/admin/shipping-methods/${method.id}`, {
        method: "PATCH",
        body: JSON.stringify({ status }),
      });
      setNotice(
        `配送方式 ${method.name} 已${status === "active" ? "启用" : "停用"}`,
      );
      await load();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusyId(null);
    }
  };

  return (
    <section className="admin-shipping">
      {role === "owner" ? (
        <form className="panel panel-pad" onSubmit={create}>
          <div className="section-head" style={{ marginBottom: 10 }}>
            <div>
              <h3 style={{ margin: 0 }}>新建配送方式</h3>
              <p>
                结算默认使用 standard；未配置时运费为 0。包邮门槛按商品小计判断。
              </p>
            </div>
          </div>
          <div className="form-grid">
            <div className="field">
              <label htmlFor="shipping-code">编码</label>
              <input
                id="shipping-code"
                data-testid="shipping-code"
                value={form.code}
                onChange={(event) =>
                  setForm({
                    ...form,
                    code: event.target.value.toUpperCase(),
                  })
                }
                maxLength={32}
                placeholder="如 EXPRESS"
                required
              />
            </div>
            <div className="field">
              <label htmlFor="shipping-name">名称</label>
              <input
                id="shipping-name"
                data-testid="shipping-name"
                value={form.name}
                onChange={(event) =>
                  setForm({ ...form, name: event.target.value })
                }
                maxLength={120}
                placeholder="如 顺丰加急"
                required
              />
            </div>
            <div className="field">
              <label htmlFor="shipping-fee">运费（元）</label>
              <input
                id="shipping-fee"
                data-testid="shipping-fee"
                type="number"
                min="0"
                step="0.01"
                value={form.fee}
                onChange={(event) =>
                  setForm({ ...form, fee: event.target.value })
                }
              />
            </div>
            <div className="field">
              <label htmlFor="shipping-free-over">
                包邮门槛（元，留空不包邮）
              </label>
              <input
                id="shipping-free-over"
                data-testid="shipping-free-over"
                type="number"
                min="0"
                step="0.01"
                value={form.free_over}
                onChange={(event) =>
                  setForm({ ...form, free_over: event.target.value })
                }
              />
            </div>
            <div className="field">
              <label htmlFor="shipping-sort">排序</label>
              <input
                id="shipping-sort"
                data-testid="shipping-sort"
                type="number"
                min="0"
                value={form.sort_order}
                onChange={(event) =>
                  setForm({ ...form, sort_order: event.target.value })
                }
              />
            </div>
          </div>
          <div className="proposal-actions" style={{ marginTop: 12 }}>
            <button
              className="btn btn-primary"
              type="submit"
              data-testid="create-shipping-method"
              disabled={busy}
            >
              {busy ? "创建中" : "创建配送方式"}
            </button>
          </div>
        </form>
      ) : (
        <div className="notice notice-warn">
          客服账号只能查看配送方式；创建和停用需要店主权限。
        </div>
      )}

      {notice ? <div className="notice notice-success">{notice}</div> : null}
      {error ? <div className="notice notice-error">{error}</div> : null}

      {loading ? (
        <div className="loading">加载配送方式…</div>
      ) : items.length === 0 ? (
        <div className="empty-state">还没有配送方式。</div>
      ) : (
        <div className="admin-grid">
          {items.map((method) => (
            <article
              className="proposal-card"
              key={method.id}
              data-testid={`shipping-method-${method.id}`}
            >
              <div className="proposal-head">
                <div className="proposal-title">
                  <strong>{method.name}</strong>
                  <span className="badge badge-neutral">{method.code}</span>
                  <StatusBadge
                    label={method.status === "active" ? "启用" : "停用"}
                    tone={
                      method.status === "active"
                        ? "badge-ok"
                        : "badge-muted"
                    }
                  />
                </div>
                <span className="muted">
                  {formatDateTime(method.created_at)}
                </span>
              </div>
              <div className="proposal-meta">
                <span>运费 {(method.fee_cents / 100).toFixed(2)} 元</span>
                <span>
                  包邮门槛{" "}
                  {method.free_over_cents === null
                    ? "无"
                    : `${(method.free_over_cents / 100).toFixed(2)} 元`}
                </span>
                <span>排序 {method.sort_order}</span>
              </div>
              {role === "owner" ? (
                <div className="proposal-actions">
                  <button
                    className={
                      method.status === "active"
                        ? "btn btn-danger btn-sm"
                        : "btn btn-primary btn-sm"
                    }
                    type="button"
                    data-testid={`toggle-shipping-${method.id}`}
                    disabled={busyId === method.id}
                    onClick={() => void toggle(method)}
                  >
                    {method.status === "active" ? "停用" : "启用"}
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
