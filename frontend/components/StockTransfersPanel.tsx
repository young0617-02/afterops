"use client";

import { useCallback, useEffect, useState, type FormEvent } from "react";

import { StatusBadge } from "@/components/StatusBadge";
import { api, errorMessage } from "@/lib/api";
import { formatDateTime } from "@/lib/status";
import type { StockTransferView } from "@/lib/types";

const STATUS_LABELS: Record<string, string> = {
  in_transit: "在途",
  received: "已收货",
  cancelled: "已取消",
};

export function StockTransfersPanel() {
  const [rows, setRows] = useState<StockTransferView[]>([]);
  const [status, setStatus] = useState("all");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [form, setForm] = useState({
    sku: "",
    from_warehouse_id: "华东",
    to_warehouse_id: "华南",
    qty: "1",
    note: "",
  });
  const [cancelReasons, setCancelReasons] = useState<
    Record<number, string>
  >({});

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setRows(
        await api<StockTransferView[]>(
          `/api/admin/stock-transfers?status=${status}&limit=100`,
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

  const create = async (event: FormEvent) => {
    event.preventDefault();
    const qty = Number(form.qty);
    if (!Number.isInteger(qty) || qty <= 0) {
      setError("调拨数量必须是正整数");
      return;
    }
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const created = await api<StockTransferView>(
        "/api/admin/stock-transfers",
        {
          method: "POST",
          body: JSON.stringify({
            sku: form.sku.trim(),
            from_warehouse_id: form.from_warehouse_id.trim(),
            to_warehouse_id: form.to_warehouse_id.trim(),
            qty,
            note: form.note.trim(),
          }),
        },
      );
      setNotice(
        `调拨单 ${created.transfer_no} 已创建，${created.qty} 件在途`,
      );
      setForm((previous) => ({ ...previous, qty: "1", note: "" }));
      await load();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  const act = async (
    row: StockTransferView,
    action: "receive" | "cancel",
  ) => {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const updated = await api<StockTransferView>(
        `/api/admin/stock-transfers/${row.id}/${action}`,
        {
          method: "POST",
          ...(action === "cancel"
            ? {
                body: JSON.stringify({
                  reason: (cancelReasons[row.id] ?? "").trim(),
                }),
              }
            : {}),
        },
      );
      setNotice(
        action === "receive"
          ? `调拨单 ${updated.transfer_no} 已收货，${updated.qty} 件入库`
          : `调拨单 ${updated.transfer_no} 已取消，库存已回补调出仓`,
      );
      await load();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section
      className="panel panel-pad"
      data-testid="stock-transfers"
      style={{ marginBottom: 16 }}
    >
      <div className="section-head" style={{ marginBottom: 10 }}>
        <div>
          <h3 style={{ margin: 0 }}>调拨单（在途库存）</h3>
          <p>
            创建后调出仓立即扣减，调入仓确认收货后入库；取消回补调出仓。
          </p>
        </div>
        <select
          value={status}
          onChange={(event) => setStatus(event.target.value)}
          aria-label="按调拨状态筛选"
          data-testid="stock-transfer-filter"
        >
          <option value="all">全部</option>
          <option value="in_transit">在途</option>
          <option value="received">已收货</option>
          <option value="cancelled">已取消</option>
        </select>
      </div>

      <form className="ship-form" onSubmit={create}>
        <div className="field">
          <label htmlFor="transfer-order-sku">SKU</label>
          <input
            id="transfer-order-sku"
            data-testid="transfer-order-sku"
            value={form.sku}
            maxLength={64}
            placeholder="如 SKU-EAR-001"
            onChange={(event) =>
              setForm((previous) => ({
                ...previous,
                sku: event.target.value,
              }))
            }
          />
        </div>
        <div className="field">
          <label htmlFor="transfer-order-from">调出仓</label>
          <input
            id="transfer-order-from"
            data-testid="transfer-order-from"
            value={form.from_warehouse_id}
            maxLength={64}
            onChange={(event) =>
              setForm((previous) => ({
                ...previous,
                from_warehouse_id: event.target.value,
              }))
            }
          />
        </div>
        <div className="field">
          <label htmlFor="transfer-order-to">调入仓</label>
          <input
            id="transfer-order-to"
            data-testid="transfer-order-to"
            value={form.to_warehouse_id}
            maxLength={64}
            onChange={(event) =>
              setForm((previous) => ({
                ...previous,
                to_warehouse_id: event.target.value,
              }))
            }
          />
        </div>
        <div className="field">
          <label htmlFor="transfer-order-qty">数量</label>
          <input
            id="transfer-order-qty"
            data-testid="transfer-order-qty"
            type="number"
            min={1}
            value={form.qty}
            onChange={(event) =>
              setForm((previous) => ({
                ...previous,
                qty: event.target.value,
              }))
            }
          />
        </div>
        <div className="field">
          <label htmlFor="transfer-order-note">备注（可选）</label>
          <input
            id="transfer-order-note"
            data-testid="transfer-order-note"
            value={form.note}
            maxLength={500}
            onChange={(event) =>
              setForm((previous) => ({
                ...previous,
                note: event.target.value,
              }))
            }
          />
        </div>
        <button
          className="btn btn-primary"
          type="submit"
          data-testid="transfer-order-submit"
          disabled={busy || !form.sku.trim()}
        >
          创建调拨单
        </button>
      </form>

      {notice ? <div className="notice notice-success">{notice}</div> : null}
      {error ? <div className="notice notice-error">{error}</div> : null}

      {loading ? (
        <div className="loading">加载调拨单…</div>
      ) : rows.length === 0 ? (
        <div className="empty-state">当前筛选下没有调拨单。</div>
      ) : (
        <table className="table">
          <thead>
            <tr>
              <th>调拨单</th>
              <th>商品</th>
              <th>调拨</th>
              <th>数量</th>
              <th>状态</th>
              <th>时间</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr
                key={row.id}
                data-testid={`stock-transfer-${row.id}`}
              >
                <td>{row.transfer_no}</td>
                <td>
                  {row.sku} · {row.title}
                </td>
                <td>
                  {row.from_warehouse_id} → {row.to_warehouse_id}
                </td>
                <td>{row.qty}</td>
                <td>
                  <StatusBadge
                    label={STATUS_LABELS[row.status] ?? row.status}
                    tone={
                      row.status === "received"
                        ? "badge-ok"
                        : row.status === "cancelled"
                          ? "badge-muted"
                          : "badge-warn"
                    }
                  />
                  {row.cancel_reason ? (
                    <span className="muted"> · {row.cancel_reason}</span>
                  ) : null}
                </td>
                <td>
                  {formatDateTime(
                    row.received_at ?? row.cancelled_at ?? row.created_at,
                  )}
                </td>
                <td>
                  {row.status === "in_transit" ? (
                    <div className="proposal-actions">
                      <button
                        className="btn btn-primary btn-sm"
                        type="button"
                        data-testid={`receive-stock-transfer-${row.id}`}
                        disabled={busy}
                        onClick={() => void act(row, "receive")}
                      >
                        确认收货
                      </button>
                      <input
                        value={cancelReasons[row.id] ?? ""}
                        onChange={(event) =>
                          setCancelReasons((previous) => ({
                            ...previous,
                            [row.id]: event.target.value,
                          }))
                        }
                        placeholder="取消原因"
                        maxLength={500}
                        aria-label={`调拨单 ${row.id} 取消原因`}
                        data-testid={`cancel-transfer-reason-${row.id}`}
                      />
                      <button
                        className="btn btn-danger btn-sm"
                        type="button"
                        data-testid={`cancel-stock-transfer-${row.id}`}
                        disabled={
                          busy ||
                          !(cancelReasons[row.id] ?? "").trim()
                        }
                        onClick={() => void act(row, "cancel")}
                      >
                        取消调拨
                      </button>
                    </div>
                  ) : (
                    <span className="muted">—</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}