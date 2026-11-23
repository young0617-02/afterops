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
import type { CustomerAddress } from "@/lib/types";

const EMPTY_FORM = {
  label: "",
  recipient_name: "",
  recipient_phone: "",
  region: "",
  city: "",
  detail: "",
  is_default: false,
};

export default function AddressesPage() {
  const { customer } = useCustomer();
  const [items, setItems] = useState<CustomerAddress[]>([]);
  const [form, setForm] = useState(EMPTY_FORM);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    if (!customer) {
      setItems([]);
      return;
    }
    try {
      setItems(await api<CustomerAddress[]>("/api/store/addresses"));
      setError("");
    } catch (err) {
      setError(errorMessage(err));
    }
  }, [customer]);

  useEffect(() => {
    void load();
  }, [load]);

  const updateField =
    (key: keyof typeof form) =>
    (event: ChangeEvent<HTMLInputElement>) => {
      const value =
        key === "is_default" ? event.target.checked : event.target.value;
      setForm((previous) => ({ ...previous, [key]: value }));
    };

  const resetForm = () => {
    setForm(EMPTY_FORM);
    setEditingId(null);
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const path = editingId
        ? `/api/store/addresses/${editingId}`
        : "/api/store/addresses";
      await api<CustomerAddress>(path, {
        method: editingId ? "PATCH" : "POST",
        body: JSON.stringify({
          ...form,
          label: form.label.trim(),
          recipient_name: form.recipient_name.trim(),
          recipient_phone: form.recipient_phone.trim(),
          region: form.region.trim(),
          city: form.city.trim(),
          detail: form.detail.trim(),
        }),
      });
      setNotice(editingId ? "地址已更新" : "地址已保存");
      resetForm();
      await load();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  const edit = (address: CustomerAddress) => {
    setEditingId(address.id);
    setForm({
      label: address.label,
      recipient_name: address.recipient_name,
      recipient_phone: address.recipient_phone,
      region: address.region,
      city: address.city,
      detail: address.detail,
      is_default: address.is_default,
    });
    setNotice("");
    setError("");
  };

  const setDefault = async (address: CustomerAddress) => {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await api(`/api/store/addresses/${address.id}/default`, {
        method: "POST",
      });
      setNotice(`已将「${address.label || address.recipient_name}」设为默认地址`);
      await load();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  const remove = async (address: CustomerAddress) => {
    if (!window.confirm(`删除地址「${address.label || address.recipient_name}」？`)) {
      return;
    }
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await api(`/api/store/addresses/${address.id}`, { method: "DELETE" });
      if (editingId === address.id) resetForm();
      setNotice("地址已删除");
      await load();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  if (!customer) {
    return (
      <div className="empty-state">
        请先在右上角用手机号登录，再管理收货地址。
      </div>
    );
  }

  return (
    <>
      <div className="section-head">
        <div>
          <h2>地址簿</h2>
          <p>保存常用收货地址，结算时可直接选择；最多保存 10 个。</p>
        </div>
        <Link className="btn btn-ghost" href="/cart">
          去结算
        </Link>
      </div>

      {notice ? <div className="notice notice-success">{notice}</div> : null}
      {error ? <div className="notice notice-error">{error}</div> : null}

      <div className="cart-layout">
        <section className="panel panel-pad">
          <div className="section-head" style={{ marginBottom: 10 }}>
            <div>
              <h3 style={{ margin: 0 }}>
                {editingId ? "编辑地址" : "新增地址"}
              </h3>
              <p>姓名、手机号、城市与详细地址为必填项。</p>
            </div>
          </div>
          <form onSubmit={submit}>
            <div className="form-grid">
              <div className="field">
                <label htmlFor="address_label">标签</label>
                <input
                  id="address_label"
                  value={form.label}
                  onChange={updateField("label")}
                  maxLength={40}
                  placeholder="家 / 公司"
                  data-testid="address-label"
                />
              </div>
              <div className="field">
                <label htmlFor="address_name">收货人</label>
                <input
                  id="address_name"
                  value={form.recipient_name}
                  onChange={updateField("recipient_name")}
                  required
                  maxLength={120}
                  data-testid="address-name"
                />
              </div>
              <div className="field">
                <label htmlFor="address_phone">手机号</label>
                <input
                  id="address_phone"
                  value={form.recipient_phone}
                  onChange={updateField("recipient_phone")}
                  required
                  maxLength={11}
                  inputMode="tel"
                  data-testid="address-phone"
                />
              </div>
              <div className="field">
                <label htmlFor="address_region">区域</label>
                <input
                  id="address_region"
                  value={form.region}
                  onChange={updateField("region")}
                  maxLength={80}
                  placeholder="如 华东"
                  data-testid="address-region"
                />
              </div>
              <div className="field">
                <label htmlFor="address_city">城市</label>
                <input
                  id="address_city"
                  value={form.city}
                  onChange={updateField("city")}
                  required
                  maxLength={80}
                  data-testid="address-city"
                />
              </div>
              <div className="field full">
                <label htmlFor="address_detail">详细地址</label>
                <input
                  id="address_detail"
                  value={form.detail}
                  onChange={updateField("detail")}
                  required
                  maxLength={255}
                  data-testid="address-detail"
                />
              </div>
              <label
                className="field full"
                style={{ flexDirection: "row", alignItems: "center", gap: 8 }}
              >
                <input
                  type="checkbox"
                  checked={form.is_default}
                  onChange={updateField("is_default")}
                  data-testid="address-default"
                />
                <span>设为默认地址</span>
              </label>
            </div>
            <div className="proposal-actions" style={{ marginTop: 14 }}>
              <button
                className="btn btn-primary"
                type="submit"
                disabled={busy}
                data-testid="address-save"
              >
                {busy ? "保存中" : editingId ? "保存修改" : "保存地址"}
              </button>
              {editingId ? (
                <button
                  className="btn btn-ghost"
                  type="button"
                  onClick={resetForm}
                  disabled={busy}
                >
                  取消编辑
                </button>
              ) : null}
            </div>
          </form>
        </section>

        <section className="panel panel-pad">
          <div className="section-head" style={{ marginBottom: 10 }}>
            <div>
              <h3 style={{ margin: 0 }}>已保存地址</h3>
              <p>默认地址会优先用于结算预填。</p>
            </div>
            <span className="badge badge-neutral">{items.length} 个</span>
          </div>
          {items.length === 0 ? (
            <div className="empty-state">还没有保存的地址。</div>
          ) : (
            <div className="admin-grid">
              {items.map((address) => (
                <article
                  className="proposal-card"
                  key={address.id}
                  data-testid={`address-${address.id}`}
                >
                  <div className="proposal-head">
                    <div className="proposal-title">
                      <strong>{address.label || address.recipient_name}</strong>
                      {address.is_default ? (
                        <span className="badge badge-ok">默认</span>
                      ) : null}
                    </div>
                  </div>
                  <div className="proposal-meta">
                    <span>{address.recipient_name}</span>
                    <span>{address.recipient_phone}</span>
                    <span>
                      {address.region} {address.city}
                    </span>
                  </div>
                  <p className="after-sales-reason">{address.detail}</p>
                  <div className="proposal-actions">
                    {!address.is_default ? (
                      <button
                        className="btn btn-ghost btn-sm"
                        type="button"
                        disabled={busy}
                        data-testid={`address-default-${address.id}`}
                        onClick={() => void setDefault(address)}
                      >
                        设为默认
                      </button>
                    ) : null}
                    <button
                      className="btn btn-ghost btn-sm"
                      type="button"
                      disabled={busy}
                      data-testid={`address-edit-${address.id}`}
                      onClick={() => edit(address)}
                    >
                      编辑
                    </button>
                    <button
                      className="btn btn-danger btn-sm"
                      type="button"
                      disabled={busy}
                      data-testid={`address-delete-${address.id}`}
                      onClick={() => void remove(address)}
                    >
                      删除
                    </button>
                  </div>
                </article>
              ))}
            </div>
          )}
        </section>
      </div>
    </>
  );
}
