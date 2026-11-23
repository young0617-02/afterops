"use client";

import { useState, type FormEvent } from "react";

import { api, errorMessage } from "@/lib/api";

type ActionType = "refund" | "compensation" | "address_change";

const ACTIONS: { value: ActionType; label: string; notice: string }[] = [
  { value: "refund", label: "退款", notice: "退款提案" },
  { value: "compensation", label: "补偿", notice: "补偿提案" },
  { value: "address_change", label: "改地址", notice: "改地址提案" },
];

type ManualActionResult = {
  proposal_id: number;
  action_type: string;
  order_no: string;
  status: string;
  amount_display: string | null;
};

export function AdminManualAction({
  onCreated,
}: {
  onCreated: (message: string) => void;
}) {
  const [actionType, setActionType] = useState<ActionType>("refund");
  const [orderNo, setOrderNo] = useState("");
  const [amount, setAmount] = useState("");
  const [newAddress, setNewAddress] = useState("");
  const [reason, setReason] = useState("店主代顾客发起售后");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");

  const action = ACTIONS.find((item) => item.value === actionType) ?? ACTIONS[0];

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    let amountCents: number | undefined;
    if (actionType !== "address_change" && amount.trim()) {
      const value = Number(amount);
      if (!Number.isFinite(value) || value <= 0) {
        setError("金额必须大于 0");
        return;
      }
      amountCents = Math.round(value * 100);
    }
    if (actionType === "compensation" && amountCents === undefined) {
      setError("补偿金额必填");
      return;
    }
    if (actionType === "address_change" && !newAddress.trim()) {
      setError("请填写新地址");
      return;
    }
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const result = await api<ManualActionResult>(
        "/api/admin/action-proposals",
        {
          method: "POST",
          body: JSON.stringify({
            action_type: actionType,
            order_no: orderNo.trim(),
            amount_cents: amountCents,
            reason: reason.trim() || undefined,
            new_address_text:
              actionType === "address_change" ? newAddress.trim() : undefined,
          }),
        },
      );
      const message = `${action.notice} #${result.proposal_id} 已创建${
        result.amount_display ? `（${result.amount_display} 元）` : ""
      }，请在列表中批准`;
      setNotice(message);
      setOrderNo("");
      setAmount("");
      setNewAddress("");
      setReason("店主代顾客发起售后");
      onCreated(message);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      {notice ? <div className="notice notice-success">{notice}</div> : null}
      {error ? <div className="notice notice-error">{error}</div> : null}
      <details className="product-create" style={{ marginBottom: 14 }}>
        <summary>代顾客发起售后动作</summary>
        <form className="product-edit-form" onSubmit={submit}>
          <div className="field">
            <label htmlFor="manual-action-type">类型</label>
            <select
              id="manual-action-type"
              data-testid="manual-action-type"
              value={actionType}
              onChange={(event) =>
                setActionType(event.target.value as ActionType)
              }
            >
              {ACTIONS.map((item) => (
                <option key={item.value} value={item.value}>
                  {item.label}
                </option>
              ))}
            </select>
          </div>
          <div className="field">
            <label htmlFor="manual-action-order">订单号</label>
            <input
              id="manual-action-order"
              data-testid="manual-action-order"
              value={orderNo}
              onChange={(event) => setOrderNo(event.target.value)}
              placeholder="ORD-2026..."
              maxLength={32}
              required
            />
          </div>
          {actionType === "address_change" ? (
            <div className="field product-full">
              <label htmlFor="manual-action-address">新地址</label>
              <input
                id="manual-action-address"
                data-testid="manual-action-address"
                value={newAddress}
                onChange={(event) => setNewAddress(event.target.value)}
                maxLength={255}
                placeholder="省市区与详细地址"
                required
              />
            </div>
          ) : (
            <div className="field">
              <label htmlFor="manual-action-amount">
                金额（元，{actionType === "refund" ? "留空为全部可退" : "必填"}）
              </label>
              <input
                id="manual-action-amount"
                data-testid="manual-action-amount"
                type="number"
                min="0.01"
                step="0.01"
                value={amount}
                onChange={(event) => setAmount(event.target.value)}
                placeholder="如 100.00"
                required={actionType === "compensation"}
              />
            </div>
          )}
          <div className="field product-full">
            <label htmlFor="manual-action-reason">原因 / 备注</label>
            <input
              id="manual-action-reason"
              data-testid="manual-action-reason"
              value={reason}
              onChange={(event) => setReason(event.target.value)}
              maxLength={1000}
            />
          </div>
          <button
            className="btn btn-primary"
            type="submit"
            data-testid="manual-action-submit"
            disabled={busy || !orderNo.trim()}
          >
            {busy ? "创建中" : `创建${action.notice}`}
          </button>
        </form>
        <p className="muted">
          创建后仍是待审批提案；需要你在提案列表批准后才会执行。
        </p>
      </details>
    </>
  );
}
