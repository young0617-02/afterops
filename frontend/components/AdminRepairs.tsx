"use client";

import { useCallback, useEffect, useState } from "react";

import { StatusBadge } from "@/components/StatusBadge";
import { api, errorMessage } from "@/lib/api";
import { formatDateTime, repairQuoteStatusLabel, repairStatusLabel } from "@/lib/status";
import type { RepairView } from "@/lib/types";

const FILTERS = [
  { value: "active", label: "待处理" },
  { value: "requested", label: "待审核" },
  { value: "approved", label: "待回寄" },
  { value: "in_transit", label: "回寄中" },
  { value: "received", label: "维修中" },
  { value: "return_shipped", label: "待顾客签收" },
  { value: "completed", label: "已完成" },
  { value: "rejected", label: "已驳回" },
  { value: "withdrawn", label: "已撤回" },
  { value: "all", label: "全部" },
];

function quoteTone(status: string): string {
  if (status === "paid") return "badge-ok";
  if (status === "declined") return "badge-muted";
  return "badge-warn";
}

function formatCents(value: number | null): string {
  if (value === null) return "—";
  return (value / 100).toFixed(2);
}

export function AdminRepairs({ role }: { role: "owner" | "agent" }) {
  const [items, setItems] = useState<RepairView[]>([]);
  const [status, setStatus] = useState("active");
  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [rejectReasons, setRejectReasons] = useState<Record<number, string>>(
    {},
  );
  const [completionNotes, setCompletionNotes] = useState<
    Record<number, string>
  >({});
  const [returnCarriers, setReturnCarriers] = useState<
    Record<number, string>
  >({});
  const [returnTrackingNos, setReturnTrackingNos] = useState<
    Record<number, string>
  >({});
  const [partsCosts, setPartsCosts] = useState<Record<number, string>>({});
  const [partsNotes, setPartsNotes] = useState<Record<number, string>>({});
  const [quoteAmounts, setQuoteAmounts] = useState<Record<number, string>>({});
  const [quoteNotes, setQuoteNotes] = useState<Record<number, string>>({});

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setItems(
        await api<RepairView[]>(
          `/api/admin/repairs?status=${status}`,
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

  const act = async (
    item: RepairView,
    action: "approve" | "reject" | "receive" | "complete",
  ) => {
    setBusyId(item.id);
    setError("");
    setNotice("");
    try {
      const partsInput = (partsCosts[item.id] ?? "").trim();
      const partsCost =
        partsInput === "" ? 0 : Math.round(Number(partsInput) * 100);
      if (!Number.isFinite(partsCost) || partsCost < 0) {
        throw new Error("配件成本必须是非负数字");
      }
      const body =
        action === "reject"
          ? { reason: rejectReasons[item.id] ?? "" }
          : action === "complete"
            ? {
                resolution_note: completionNotes[item.id] ?? "",
                return_carrier: (returnCarriers[item.id] ?? "").trim(),
                return_tracking_no: (
                  returnTrackingNos[item.id] ?? ""
                ).trim(),
                parts_cost_cents: partsCost,
                parts_note: (partsNotes[item.id] ?? "").trim(),
              }
            : undefined;
      const result = await api<RepairView>(
        `/api/admin/repairs/${item.id}/${action}`,
        { method: "POST", body: body ? JSON.stringify(body) : undefined },
      );
      setNotice(
        action === "approve"
          ? `维修 #${item.id} 已批准，等待顾客回寄`
          : action === "reject"
            ? `维修 #${item.id} 已驳回`
            : action === "receive"
              ? `维修 #${item.id} 已收货，处理中`
              : `维修 #${item.id} 已完成并寄回，等待顾客确认`,
      );
      await load();
      return result;
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusyId(null);
    }
  };

  const quote = async (item: RepairView) => {
    const amount = Math.round(
      Number(quoteAmounts[item.id] ?? "") * 100,
    );
    const note = (quoteNotes[item.id] ?? "").trim();
    if (!Number.isFinite(amount) || amount <= 0) {
      setError("请输入大于 0 的维修报价金额");
      return;
    }
    if (!note) {
      setError("请填写报价说明");
      return;
    }
    setBusyId(item.id);
    setError("");
    setNotice("");
    const isRevision = item.quote_status !== "none";
    try {
      await api(`/api/admin/repairs/${item.id}/quote`, {
        method: "POST",
        body: JSON.stringify({ amount_cents: amount, note }),
      });
      setNotice(
        isRevision
          ? `维修 #${item.id} 报价已更新，等待顾客确认`
          : `维修 #${item.id} 报价已发送，等待顾客确认`,
      );
      setQuoteAmounts((previous) => ({ ...previous, [item.id]: "" }));
      setQuoteNotes((previous) => ({ ...previous, [item.id]: "" }));
      await load();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusyId(null);
    }
  };

  return (
    <section className="admin-repairs">
      <div className="audit-summary">
        <span className="muted">
          维修不产生退款；如需赔付或退货，请走对应流程。
        </span>
      </div>
      <div className="fulfillment-toolbar">
        <select
          value={status}
          onChange={(event) => setStatus(event.target.value)}
          aria-label="按维修状态筛选"
          data-testid="repair-filter"
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
          客服账号只能查看维修进度；批准、收货和完成维修需要店主权限。
        </div>
      ) : null}
      {notice ? <div className="notice notice-success">{notice}</div> : null}
      {error ? <div className="notice notice-error">{error}</div> : null}

      {loading ? (
        <div className="loading">加载维修申请…</div>
      ) : items.length === 0 ? (
        <div className="empty-state">当前筛选下没有维修申请。</div>
      ) : (
        <div className="admin-grid">
          {items.map((item) => (
            <article
              className="proposal-card"
              key={item.id}
              data-testid={`admin-repair-${item.id}`}
            >
              <div className="proposal-head">
                <div className="proposal-title">
                  <strong>
                    维修 #{item.id} · {item.sku}
                  </strong>
                  <StatusBadge
                    label={repairStatusLabel(item.status)}
                    tone={
                      item.status === "completed"
                        ? "badge-ok"
                        : item.status === "rejected" ||
                            item.status === "withdrawn"
                          ? "badge-muted"
                          : "badge-warn"
                    }
                  />
                </div>
                <span className="muted">
                  {formatDateTime(item.created_at)}
                </span>
              </div>
              <div className="proposal-meta">
                <span>订单 {item.order_no}</span>
                <span>{item.title}</span>
                {item.tracking_no ? (
                  <span>
                    回寄 {item.carrier} · {item.tracking_no}
                  </span>
                ) : null}
                {item.return_tracking_no ? (
                  <span>
                    维修寄回 {item.return_carrier} ·{" "}
                    {item.return_tracking_no}
                  </span>
                ) : null}
              </div>
              <p className="after-sales-reason">故障：{item.issue}</p>
              {item.attachment_ids.length > 0 ? (
                <div className="attachment-grid">
                  {item.attachment_ids.map((attachmentId) => (
                    <a
                      key={attachmentId}
                      href={`/api/admin/attachments/${attachmentId}`}
                      target="_blank"
                      rel="noreferrer"
                    >
                      <img
                        src={`/api/admin/attachments/${attachmentId}`}
                        alt="维修凭证"
                      />
                    </a>
                  ))}
                </div>
              ) : null}
              {item.rejected_reason ? (
                <div className="notice notice-warn">
                  驳回原因：{item.rejected_reason}
                </div>
              ) : null}
              {item.resolution_note ? (
                <p className="muted">处理结果：{item.resolution_note}</p>
              ) : null}

              {item.quote_status !== "none" ? (
                <div
                  className="repair-quote"
                  data-testid={`admin-repair-quote-${item.id}`}
                >
                  <p className="proposal-meta">
                    <span>维修报价</span>
                    <StatusBadge
                      label={repairQuoteStatusLabel(item.quote_status)}
                      tone={quoteTone(item.quote_status)}
                    />
                    {item.quote_revised_at ? (
                      <StatusBadge label="已更新" tone="badge-neutral" />
                    ) : null}
                    <span>{formatCents(item.quote_cents)} 元</span>
                    {item.quote_payment_ref ? (
                      <span>支付单 {item.quote_payment_ref}</span>
                    ) : null}
                  </p>
                  <p className="muted">报价说明：{item.quote_note}</p>
                  {item.parts_cost_cents !== null &&
                  item.parts_cost_cents > 0 ? (
                    <p
                      className="muted"
                      data-testid={`repair-parts-cost-view-${item.id}`}
                    >
                      配件成本 {formatCents(item.parts_cost_cents)} 元
                      {item.quote_cents !== null
                        ? ` · 维修毛利 ${formatCents(
                            item.quote_cents - item.parts_cost_cents,
                          )} 元`
                        : ""}
                      {item.parts_note ? ` · ${item.parts_note}` : ""}
                    </p>
                  ) : null}
                  {item.quote_status === "paid" ? (
                    <p className="muted">顾客已支付，可完成维修并寄回。</p>
                  ) : null}
                  {item.quote_status === "pending" ? (
                    <p className="muted">
                      等待顾客确认；可调整金额后重新发送。
                    </p>
                  ) : null}
                  {item.quote_status === "declined" ? (
                    <p className="muted">
                      顾客已拒绝报价，可调整价格重新报价，或按原样寄回并完成。
                    </p>
                  ) : null}
                </div>
              ) : null}
              {item.quote_status === "none" &&
              item.parts_cost_cents !== null &&
              item.parts_cost_cents > 0 ? (
                <p
                  className="muted"
                  data-testid={`repair-parts-cost-view-${item.id}`}
                >
                  配件成本 {formatCents(item.parts_cost_cents)} 元
                  {item.parts_note ? ` · ${item.parts_note}` : ""}
                </p>
              ) : null}

              {role === "owner" &&
              item.status === "received" &&
              ["none", "pending", "declined"].includes(
                item.quote_status,
              ) ? (
                <div className="proposal-actions">
                  <input
                    value={
                      quoteAmounts[item.id] ??
                      (item.quote_status === "none" ||
                      item.quote_cents === null
                        ? ""
                        : (item.quote_cents / 100).toFixed(2))
                    }
                    onChange={(event) =>
                      setQuoteAmounts((previous) => ({
                        ...previous,
                        [item.id]: event.target.value,
                      }))
                    }
                    placeholder="维修费（元）"
                    inputMode="decimal"
                    data-testid={`repair-quote-amount-${item.id}`}
                  />
                  <input
                    value={
                      quoteNotes[item.id] ??
                      (item.quote_status === "none" ? "" : item.quote_note)
                    }
                    onChange={(event) =>
                      setQuoteNotes((previous) => ({
                        ...previous,
                        [item.id]: event.target.value,
                      }))
                    }
                    placeholder="报价说明，如：更换主板"
                    maxLength={1000}
                    data-testid={`repair-quote-note-${item.id}`}
                  />
                  <button
                    className="btn btn-primary btn-sm"
                    type="button"
                    data-testid={`issue-repair-quote-${item.id}`}
                    disabled={busyId === item.id}
                    onClick={() => void quote(item)}
                  >
                    {item.quote_status === "none" ? "发送报价" : "更新报价"}
                  </button>
                </div>
              ) : null}

              {role === "owner" && item.status === "requested" ? (
                <div className="proposal-actions">
                  <button
                    className="btn btn-primary btn-sm"
                    type="button"
                    data-testid={`approve-repair-${item.id}`}
                    disabled={busyId === item.id}
                    onClick={() => void act(item, "approve")}
                  >
                    批准维修
                  </button>
                  <div className="reject-row">
                    <input
                      value={rejectReasons[item.id] ?? ""}
                      onChange={(event) =>
                        setRejectReasons((previous) => ({
                          ...previous,
                          [item.id]: event.target.value,
                        }))
                      }
                      placeholder="驳回原因（可选）"
                      maxLength={1000}
                      aria-label={`维修 ${item.id} 驳回原因`}
                    />
                    <button
                      className="btn btn-danger btn-sm"
                      type="button"
                      data-testid={`reject-repair-${item.id}`}
                      disabled={busyId === item.id}
                      onClick={() => void act(item, "reject")}
                    >
                      驳回
                    </button>
                  </div>
                </div>
              ) : null}

              {role === "owner" &&
              (item.status === "approved" || item.status === "in_transit") ? (
                <div className="proposal-actions">
                  <button
                    className="btn btn-primary btn-sm"
                    type="button"
                    data-testid={`receive-repair-${item.id}`}
                    disabled={busyId === item.id}
                    onClick={() => void act(item, "receive")}
                  >
                    确认收货
                  </button>
                </div>
              ) : null}

              {role === "owner" && item.status === "received" ? (
                <div className="proposal-actions">
                  <input
                    value={completionNotes[item.id] ?? ""}
                    onChange={(event) =>
                      setCompletionNotes((previous) => ({
                        ...previous,
                        [item.id]: event.target.value,
                      }))
                    }
                    placeholder="处理结果，如：已更换电池"
                    maxLength={1000}
                    aria-label={`维修 ${item.id} 处理结果`}
                    data-testid={`repair-completion-${item.id}`}
                  />
                  <input
                    value={returnCarriers[item.id] ?? ""}
                    onChange={(event) =>
                      setReturnCarriers((previous) => ({
                        ...previous,
                        [item.id]: event.target.value,
                      }))
                    }
                    placeholder="寄回承运商"
                    maxLength={64}
                    aria-label={`维修 ${item.id} 寄回承运商`}
                    data-testid={`repair-return-carrier-${item.id}`}
                  />
                  <input
                    value={returnTrackingNos[item.id] ?? ""}
                    onChange={(event) =>
                      setReturnTrackingNos((previous) => ({
                        ...previous,
                        [item.id]: event.target.value,
                      }))
                    }
                    placeholder="寄回运单号"
                    maxLength={64}
                    aria-label={`维修 ${item.id} 寄回运单号`}
                    data-testid={`repair-return-tracking-${item.id}`}
                  />
                  <input
                    value={partsCosts[item.id] ?? ""}
                    onChange={(event) =>
                      setPartsCosts((previous) => ({
                        ...previous,
                        [item.id]: event.target.value,
                      }))
                    }
                    placeholder="配件成本（元，可选）"
                    inputMode="decimal"
                    aria-label={`维修 ${item.id} 配件成本`}
                    data-testid={`repair-parts-cost-${item.id}`}
                  />
                  <input
                    value={partsNotes[item.id] ?? ""}
                    onChange={(event) =>
                      setPartsNotes((previous) => ({
                        ...previous,
                        [item.id]: event.target.value,
                      }))
                    }
                    placeholder="配件说明（可选）"
                    maxLength={500}
                    aria-label={`维修 ${item.id} 配件说明`}
                    data-testid={`repair-parts-note-${item.id}`}
                  />
                  <button
                    className="btn btn-primary btn-sm"
                    type="button"
                    data-testid={`complete-repair-${item.id}`}
                    disabled={
                      busyId === item.id ||
                      item.quote_status === "pending" ||
                      item.quote_status === "accepted" ||
                      !(completionNotes[item.id] ?? "").trim() ||
                      !(returnCarriers[item.id] ?? "").trim() ||
                      !(returnTrackingNos[item.id] ?? "").trim()
                    }
                    onClick={() => void act(item, "complete")}
                  >
                    完成并寄回
                  </button>
                </div>
              ) : null}

              {item.status === "return_shipped" ? (
                <p className="muted">
                  维修已完成并寄回，等待顾客确认收到。
                </p>
              ) : null}
            </article>
          ))}
        </div>
      )}
    </section>
  );
}
