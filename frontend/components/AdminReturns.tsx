"use client";

import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";

import { StatusBadge } from "@/components/StatusBadge";
import { AdminReturnAnalytics } from "@/components/AdminReturnAnalytics";
import { api, errorMessage } from "@/lib/api";
import { formatDateTime, returnReasonLabel, returnStatusLabel } from "@/lib/status";
import type {
  AdminReturnList,
  ReturnPolicy,
  ReturnRequestView,
} from "@/lib/types";

const RETURN_TRACKING_NEXT: Record<string, string> = {
  registered: "picked_up",
  picked_up: "in_transit",
  in_transit: "out_for_delivery",
  out_for_delivery: "delivered",
};

const RETURN_TRACKING_LABELS: Record<string, string> = {
  registered: "已登记回寄",
  picked_up: "已揽收",
  in_transit: "运输中",
  out_for_delivery: "派送中",
  delivered: "已到达退货仓",
};

const FILTERS = [
  { value: "active", label: "待处理" },
  { value: "requested", label: "待审核" },
  { value: "approved", label: "待回寄" },
  { value: "in_transit", label: "已寄回" },
  { value: "received", label: "已收货" },
  { value: "exchange_cancelled", label: "换货已取消" },
  { value: "appeal", label: "扣减申诉" },
  { value: "pickup", label: "上门取件" },
  { value: "rejected", label: "已驳回" },
  { value: "withdrawn", label: "已撤回" },
  { value: "all", label: "全部" },
];

function formatCents(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return (value / 100).toFixed(2);
}

function returnTone(status: string): string {
  if (status === "received") return "badge-ok";
  if (status === "rejected" || status === "withdrawn") return "badge-muted";
  return "badge-warn";
}

function appealStatusLabel(status: string): string {
  if (status === "pending") return "待处理";
  if (status === "accepted") return "已支持";
  if (status === "rejected") return "未通过";
  if (status === "withdrawn") return "已撤回";
  return status;
}

function appealTone(status: string): string {
  if (status === "accepted") return "badge-ok";
  if (status === "rejected" || status === "withdrawn") return "badge-muted";
  return "badge-warn";
}

function pickupStatusLabel(status: string): string {
  if (status === "scheduled") return "待上门";
  if (status === "picked_up") return "已揽收";
  if (status === "cancelled") return "已取消";
  return status;
}

function pickupTone(status: string): string {
  if (status === "picked_up") return "badge-ok";
  if (status === "cancelled") return "badge-muted";
  return "badge-warn";
}

const RESPONSIBILITY_LABELS: Record<string, string> = {
  merchant: "商家责任",
  customer: "顾客责任",
  logistics: "物流责任",
  other: "其他责任",
};

export function AdminReturns({ role }: { role: "owner" | "agent" }) {
  const [items, setItems] = useState<ReturnRequestView[]>([]);
  const [counts, setCounts] = useState<Record<string, number>>({});
  const [status, setStatus] = useState("active");
  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [rejectReasons, setRejectReasons] = useState<Record<number, string>>({});
  const [restockFlags, setRestockFlags] = useState<Record<number, boolean>>({});
  const [dispositions, setDispositions] = useState<
    Record<number, Record<number, "restock" | "damaged">>
  >({});
  const [responsibilities, setResponsibilities] = useState<
    Record<number, Record<number, string>>
  >({});
  const [deductions, setDeductions] = useState<Record<number, string>>({});
  const [itemDeductions, setItemDeductions] = useState<
    Record<number, Record<number, string>>
  >({});
  const [inspectionNotes, setInspectionNotes] = useState<
    Record<number, string>
  >({});
  const [exceptionReasons, setExceptionReasons] = useState<
    Record<number, string>
  >({});
  const [appealAmounts, setAppealAmounts] = useState<Record<number, string>>(
    {},
  );
  const [appealNotes, setAppealNotes] = useState<Record<number, string>>({});
  const [appealBusyId, setAppealBusyId] = useState<number | null>(null);
  const [pickupNotes, setPickupNotes] = useState<Record<number, string>>({});
  const [pickupBusyId, setPickupBusyId] = useState<number | null>(null);
  const [exchangeCancelBusyId, setExchangeCancelBusyId] = useState<
    number | null
  >(null);
  const [exchangeExtendBusyId, setExchangeExtendBusyId] = useState<
    number | null
  >(null);
  const [policy, setPolicy] = useState<ReturnPolicy | null>(null);
  const [policyWindow, setPolicyWindow] = useState("7");
  const [policyFee, setPolicyFee] = useState("0");
  const [policyExchange, setPolicyExchange] = useState(true);
  const [policyCategories, setPolicyCategories] = useState("");
  const [policyWindows, setPolicyWindows] = useState("");
  const [policyBusy, setPolicyBusy] = useState(false);
  const loadSeq = useRef(0);

  const load = useCallback(async () => {
    const seq = ++loadSeq.current;
    setLoading(true);
    try {
      const [body, policyBody] = await Promise.all([
        api<AdminReturnList>(
          `/api/admin/returns?status=${status}&limit=100`,
        ),
        api<ReturnPolicy>("/api/admin/return-policy"),
      ]);
      if (seq !== loadSeq.current) return;
      setItems(body.items);
      setCounts(body.counts);
      setPolicy(policyBody);
      setPolicyWindow(String(policyBody.window_days));
      setPolicyFee((policyBody.return_shipping_fee_cents / 100).toFixed(2));
      setPolicyExchange(policyBody.allow_exchange);
      setPolicyCategories(policyBody.final_sale_categories.join("、"));
      setPolicyWindows(
        policyBody.category_windows
          .map((item) => `${item.category}=${item.window_days}`)
          .join("\n"),
      );
      setError("");
    } catch (err) {
      if (seq === loadSeq.current) setError(errorMessage(err));
    } finally {
      if (seq === loadSeq.current) setLoading(false);
    }
  }, [status]);

  useEffect(() => {
    void load();
  }, [load]);

  const savePolicy = async (event: FormEvent) => {
    event.preventDefault();
    if (!policy) return;
    setPolicyBusy(true);
    setError("");
    setNotice("");
    try {
      const categoryWindows = policyWindows
        .split("\n")
        .map((line) => line.trim())
        .filter(Boolean)
        .map((line) => {
          const match = line.match(/^(.+?)[=:：]\s*(\d+)$/);
          if (!match) {
            throw new Error(
              `无法识别类目窗口「${line}」，请使用 类目=天数`,
            );
          }
          return {
            category: match[1].trim(),
            window_days: Number(match[2]),
          };
        });
      const updated = await api<ReturnPolicy>("/api/admin/return-policy", {
        method: "PATCH",
        body: JSON.stringify({
          window_days: Number(policyWindow),
          return_shipping_fee_cents: Math.round(Number(policyFee) * 100),
          allow_exchange: policyExchange,
          final_sale_categories: policyCategories
            .split(/[，,、\n]/)
            .map((value) => value.trim())
            .filter(Boolean),
          category_windows: categoryWindows,
        }),
      });
      setPolicy(updated);
      setPolicyWindow(String(updated.window_days));
      setPolicyFee((updated.return_shipping_fee_cents / 100).toFixed(2));
      setPolicyExchange(updated.allow_exchange);
      setPolicyCategories(updated.final_sale_categories.join("、"));
      setPolicyWindows(
        updated.category_windows
          .map((item) => `${item.category}=${item.window_days}`)
          .join("\n"),
      );
      setNotice("退货政策已保存");
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setPolicyBusy(false);
    }
  };

  const act = async (
    item: ReturnRequestView,
    action: "approve" | "reject" | "receive",
  ) => {
    setBusyId(item.id);
    setError("");
    setNotice("");
    try {
      let body: Record<string, unknown> | undefined;
      if (action === "reject") {
        body = { reason: rejectReasons[item.id] ?? "" };
      } else if (action === "receive") {
        const deductionInput = (deductions[item.id] ?? "").trim();
        const deductionCents =
          deductionInput === "" ? 0 : Math.round(Number(deductionInput) * 100);
        if (!Number.isFinite(deductionCents) || deductionCents < 0) {
          throw new Error("质检扣减金额必须是非负数字");
        }
        const itemDeductionCents = (lineId: number) => {
          const raw = (itemDeductions[item.id]?.[lineId] ?? "").trim();
          const value = raw === "" ? 0 : Math.round(Number(raw) * 100);
          if (!Number.isFinite(value) || value < 0) {
            throw new Error("逐件质检扣减金额必须是非负数字");
          }
          return value;
        };
        body = {
          ...(item.items.length > 0
            ? {
                items: item.items.map((line) => ({
                  item_id: line.id,
                  disposition:
                    dispositions[item.id]?.[line.id] ?? "restock",
                  deduction_cents: itemDeductionCents(line.id),
                  ...(responsibilities[item.id]?.[line.id]
                    ? {
                        responsibility:
                          responsibilities[item.id][line.id],
                      }
                    : {}),
                })),
              }
            : {
                restock: restockFlags[item.id] ?? true,
                deduction_cents: deductionCents,
              }),
          ...(item.resolution === "refund"
            ? {
                inspection_note: (inspectionNotes[item.id] ?? "").trim(),
              }
            : {}),
        };
      }
      const result = await api<ReturnRequestView>(
        `/api/admin/returns/${item.id}/${action}`,
        { method: "POST", body: body ? JSON.stringify(body) : undefined },
      );
      const restocked =
        result.restocked_items?.reduce((sum, line) => sum + line.qty, 0) ?? 0;
      const damaged =
        result.damaged_items?.reduce((sum, line) => sum + line.qty, 0) ?? 0;
      const restockNote =
        (restocked > 0 ? `，回补库存 ${restocked} 件` : "") +
        (damaged > 0 ? `，报损 ${damaged} 件` : "");
      const refundNote =
        (result.inspection_deduction_cents > 0
          ? `，质检扣减 ${formatCents(result.inspection_deduction_cents)} 元`
          : "") +
        (result.refund_amount_cents !== null &&
        result.refund_amount_cents !== undefined &&
        result.refund_amount_cents > 0
          ? `，退款 ${formatCents(result.refund_amount_cents)} 元`
          : "");
      if (action === "approve") {
        setNotice(`退货 #${item.id} 已批准，等待顾客回寄`);
      } else if (action === "reject") {
        setNotice(`退货 #${item.id} 已驳回`);
      } else if (result.replacement_order_no && result.refund_proposal_id) {
        setNotice(
          `退货 #${item.id} 已收货${restockNote}，换货订单 ${result.replacement_order_no} 待发货；差价退款提案 #${result.refund_proposal_id} 待审批${refundNote}`,
        );
      } else if (result.replacement_order_no) {
        setNotice(
          `退货 #${item.id} 已收货${restockNote}，换货订单 ${result.replacement_order_no} 待发货`,
        );
      } else if (!result.refund_proposal_id && result.refund_amount_cents === 0) {
        setNotice(
          `退货 #${item.id} 已收货${restockNote}，扣除退货运费后无需退款`,
        );
      } else {
        setNotice(
          `退货 #${item.id} 已收货${restockNote}，退款提案 #${result.refund_proposal_id} 待审批${refundNote}`,
        );
      }
      await load();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusyId(null);
    }
  };

  const uploadInspectionPhotos = async (
    item: ReturnRequestView,
    files: File[],
  ) => {
    const remaining = 3 - item.inspection_attachment_ids.length;
    if (remaining <= 0) {
      setError("质检照片最多 3 张");
      return;
    }
    setBusyId(item.id);
    setError("");
    setNotice("");
    try {
      for (const file of files.slice(0, remaining)) {
        const form = new FormData();
        form.append("file", file);
        await api(`/api/admin/returns/${item.id}/inspection-attachments`, {
          method: "POST",
          body: form,
        });
      }
      setNotice(`退货 #${item.id} 质检照片已上传`);
      await load();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusyId(null);
    }
  };

  const reportTrackingException = async (item: ReturnRequestView) => {
    const description = (exceptionReasons[item.id] ?? "").trim();
    if (!description) return;
    setBusyId(item.id);
    setError("");
    setNotice("");
    try {
      await api(`/api/admin/returns/${item.id}/tracking/exception`, {
        method: "POST",
        body: JSON.stringify({ description }),
      });
      setNotice(`退货 #${item.id} 物流异常已登记`);
      setExceptionReasons((previous) => ({
        ...previous,
        [item.id]: "",
      }));
      await load();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusyId(null);
    }
  };

  const advanceTracking = async (item: ReturnRequestView) => {
    const next = RETURN_TRACKING_NEXT[item.shipment_status];
    if (!next) return;
    setBusyId(item.id);
    setError("");
    setNotice("");
    try {
      await api(`/api/admin/returns/${item.id}/tracking/advance`, {
        method: "POST",
        body: JSON.stringify({ status: next }),
      });
      setNotice(
        `退货 #${item.id} 物流已更新：${RETURN_TRACKING_LABELS[next]}`,
      );
      await load();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusyId(null);
    }
  };

  const resolveAppeal = async (
    item: ReturnRequestView,
    accept: boolean,
  ) => {
    const appeal = item.appeal;
    if (!appeal || appeal.status !== "pending") return;
    const note = (appealNotes[item.id] ?? "").trim();
    const amountText =
      appealAmounts[item.id] ?? (appeal.requested_cents / 100).toFixed(2);
    const approvedCents = Math.round(Number(amountText) * 100);
    if (accept && (!Number.isFinite(approvedCents) || approvedCents <= 0)) {
      setError("请输入大于 0 的核准金额");
      return;
    }
    if (!accept && !note) {
      setError("驳回申诉需填写说明");
      return;
    }
    setAppealBusyId(item.id);
    setError("");
    setNotice("");
    try {
      await api(
        `/api/admin/returns/${item.id}/appeal/${
          accept ? "accept" : "reject"
        }`,
        {
          method: "POST",
          body: JSON.stringify(
            accept
              ? { approved_cents: approvedCents, note }
              : { note },
          ),
        },
      );
      setNotice(
        accept
          ? `退货 #${item.id} 扣减申诉已支持，追加退款提案已生成`
          : `退货 #${item.id} 扣减申诉已驳回`,
      );
      setAppealAmounts((previous) => ({ ...previous, [item.id]: "" }));
      setAppealNotes((previous) => ({ ...previous, [item.id]: "" }));
      await load();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setAppealBusyId(null);
    }
  };

  const markPickupPickedUp = async (item: ReturnRequestView) => {
    setPickupBusyId(item.id);
    setError("");
    setNotice("");
    try {
      await api(`/api/admin/returns/${item.id}/pickup/picked-up`, {
        method: "POST",
      });
      setNotice(`退货 #${item.id} 上门取件已揽收`);
      await load();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setPickupBusyId(null);
    }
  };

  const cancelPickupByStaff = async (item: ReturnRequestView) => {
    const note = (pickupNotes[item.id] ?? "").trim();
    if (!note) {
      setError("取消取件需填写说明");
      return;
    }
    setPickupBusyId(item.id);
    setError("");
    setNotice("");
    try {
      await api(`/api/admin/returns/${item.id}/pickup/cancel`, {
        method: "POST",
        body: JSON.stringify({ note }),
      });
      setNotice(`退货 #${item.id} 上门取件预约已取消`);
      setPickupNotes((previous) => ({ ...previous, [item.id]: "" }));
      await load();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setPickupBusyId(null);
    }
  };

  const cancelExchangeByStaff = async (item: ReturnRequestView) => {
    if (
      !window.confirm(
        `取消换货 #${item.id}？换货订单将关闭并生成退款提案。`,
      )
    ) {
      return;
    }
    setExchangeCancelBusyId(item.id);
    setError("");
    setNotice("");
    try {
      const result = await api<ReturnRequestView>(
        `/api/admin/returns/${item.id}/exchange/cancel`,
        {
          method: "POST",
          body: JSON.stringify({ reason: "商家取消补差换货" }),
        },
      );
      setNotice(
        result.refund_proposal_id
          ? `退货 #${item.id} 换货已取消，退款提案 #${result.refund_proposal_id} 待审批`
          : `退货 #${item.id} 换货已取消，无需退款`,
      );
      await load();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setExchangeCancelBusyId(null);
    }
  };

  const extendExchangeSupplement = async (item: ReturnRequestView) => {
    setExchangeExtendBusyId(item.id);
    setError("");
    setNotice("");
    try {
      const result = await api<ReturnRequestView>(
        `/api/admin/returns/${item.id}/exchange-supplement/extend`,
        { method: "POST", body: JSON.stringify({ hours: 24 }) },
      );
      setNotice(
        `退货 #${item.id} 补差支付期限已延长至 ${
          result.exchange_payment_deadline
            ? formatDateTime(result.exchange_payment_deadline)
            : "新期限"
        }`,
      );
      await load();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setExchangeExtendBusyId(null);
    }
  };

  return (
    <section className="admin-returns">
      <form className="panel panel-pad" onSubmit={savePolicy}>
        <div className="section-head" style={{ marginBottom: 10 }}>
          <div>
            <h3 style={{ margin: 0 }}>退货政策</h3>
            <p>
              签收后窗口、退货运费、禁退类目与换货开关；保存后立即对顾客和
              AI 生效。
            </p>
          </div>
          <span className="muted">
            {policy?.updated_at
              ? `更新于 ${formatDateTime(policy.updated_at)}`
              : "默认值，尚未保存"}
          </span>
        </div>
        <div className="form-grid">
          <div className="field">
            <label htmlFor="return-policy-window">签收后可退天数</label>
            <input
              id="return-policy-window"
              data-testid="return-policy-window"
              type="number"
              min={0}
              max={90}
              value={policyWindow}
              onChange={(event) => setPolicyWindow(event.target.value)}
              disabled={role !== "owner" || !policy}
            />
          </div>
          <div className="field">
            <label htmlFor="return-policy-fee">
              退货运费（元，从退款中扣除）
            </label>
            <input
              id="return-policy-fee"
              data-testid="return-policy-fee"
              type="number"
              min={0}
              max={200}
              step="0.01"
              value={policyFee}
              onChange={(event) => setPolicyFee(event.target.value)}
              disabled={role !== "owner" || !policy}
            />
          </div>
          <div className="field">
            <label>换货</label>
            <label>
              <input
                type="checkbox"
                data-testid="return-policy-exchange"
                checked={policyExchange}
                onChange={(event) => setPolicyExchange(event.target.checked)}
                disabled={role !== "owner" || !policy}
              />{" "}
              允许顾客申请换货
            </label>
          </div>
          <div className="field full">
            <label htmlFor="return-policy-categories">
              禁退类目（用顿号或逗号分隔，可留空）
            </label>
            <input
              id="return-policy-categories"
              data-testid="return-policy-categories"
              value={policyCategories}
              onChange={(event) => setPolicyCategories(event.target.value)}
              placeholder="如：耳机、定制商品"
              disabled={role !== "owner" || !policy}
            />
          </div>
          <div className="field full">
            <label htmlFor="return-policy-windows">
              类目退货窗口（每行「类目=天数」，未配置的类目用全局天数；可留空）
            </label>
            <textarea
              id="return-policy-windows"
              data-testid="return-policy-windows"
              value={policyWindows}
              onChange={(event) => setPolicyWindows(event.target.value)}
              placeholder={"如：耳机=30\n充电器=15"}
              rows={3}
              disabled={role !== "owner" || !policy}
            />
          </div>
        </div>
        {role === "owner" ? (
          <div className="proposal-actions" style={{ marginTop: 12 }}>
            <button
              className="btn btn-primary"
              type="submit"
              disabled={policyBusy || !policy}
              data-testid="save-return-policy"
            >
              {policyBusy ? "保存中" : "保存退货政策"}
            </button>
          </div>
        ) : (
          <p className="muted">客服账号只读；修改退货政策需要店主权限。</p>
        )}
      </form>

      <AdminReturnAnalytics />

      <div className="audit-summary">
        <div className="handoff-counts">
          <span>待审核 {counts.requested ?? 0}</span>
          <span>待回寄 {counts.approved ?? 0}</span>
          <span>已寄回 {counts.in_transit ?? 0}</span>
          <span>已收货 {counts.received ?? 0}</span>
          <span>换货取消 {counts.exchange_cancelled ?? 0}</span>
          <span>扣减申诉 {counts.appeals ?? 0}</span>
          <span>上门取件 {counts.pickups ?? 0}</span>
        </div>
        <span className="muted">
          收货后自动生成退款提案，仍需店主审批
        </span>
      </div>

      <div className="fulfillment-toolbar">
        <select
          value={status}
          onChange={(event) => setStatus(event.target.value)}
          aria-label="按退货状态筛选"
          data-testid="return-filter"
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
          客服账号只能查看退货进度；审批和确认收货需要店主权限。
        </div>
      ) : null}
      {notice ? <div className="notice notice-success">{notice}</div> : null}
      {error ? <div className="notice notice-error">{error}</div> : null}

      {loading ? (
        <div className="loading">加载退货申请…</div>
      ) : items.length === 0 ? (
        <div className="empty-state">当前筛选下没有退货申请。</div>
      ) : (
        <div className="admin-grid">
          {items.map((item) => (
            <article
              className="proposal-card"
              key={item.id}
              data-testid={`admin-return-${item.id}`}
            >
              <div className="proposal-head">
                <div className="proposal-title">
                  <strong>
                    退货 #{item.id} ·{" "}
                    {item.resolution === "exchange" ? "换货" : "退货退款"}
                  </strong>
                  <StatusBadge
                    label={returnStatusLabel(item.status)}
                    tone={returnTone(item.status)}
                  />
                </div>
                <span className="muted">
                  {formatDateTime(item.created_at)}
                </span>
              </div>
              <div className="proposal-meta">
                <span>订单 {item.order_no}</span>
                <span>顾客 {item.customer_phone ?? "—"}</span>
                {item.customer_risk_level === "high" ||
                item.customer_risk_level === "medium" ? (
                  <StatusBadge
                    label={
                      item.customer_risk_level === "high"
                        ? "高风险顾客"
                        : "需关注顾客"
                    }
                    tone={
                      item.customer_risk_level === "high"
                        ? "badge-danger"
                        : "badge-warn"
                    }
                  />
                ) : null}
                {item.customer_risk_reasons.length > 0 ? (
                  <span
                    data-testid={`admin-return-risk-${item.id}`}
                    className="muted"
                  >
                    {item.customer_risk_reasons.slice(0, 2).join("；")}
                  </span>
                ) : null}
                {item.tracking_no ? (
                  <span>
                    回寄 {item.carrier} · {item.tracking_no}
                  </span>
                ) : null}
                {item.exchange_sku ? (
                  <span>换货商品 {item.exchange_sku}</span>
                ) : null}
                {item.refund_proposal_id ? (
                  <span>退款提案 #{item.refund_proposal_id}</span>
                ) : null}
                {item.refund_amount_cents !== null ? (
                  <span>
                    退款金额 {formatCents(item.refund_amount_cents)} 元
                  </span>
                ) : null}
                {item.inspection_deduction_cents > 0 ? (
                  <span>
                    质检扣减{" "}
                    {formatCents(item.inspection_deduction_cents)} 元
                  </span>
                ) : null}
                {item.items
                  .filter((line) => line.inspection_deduction_cents > 0)
                  .map((line) => (
                    <span key={`item-deduction-${item.id}-${line.id}`}>
                      {line.sku} 扣减{" "}
                      {formatCents(line.inspection_deduction_cents)} 元
                    </span>
                  ))}
                {item.inspection_note ? (
                  <span>质检说明 {item.inspection_note}</span>
                ) : null}
                {item.appeal ? (
                  <span>
                    扣减申诉 {appealStatusLabel(item.appeal.status)} · 申请{" "}
                    {formatCents(item.appeal.requested_cents)} 元
                    {item.appeal.approved_cents !== null
                      ? ` · 核准 ${formatCents(item.appeal.approved_cents)} 元`
                      : ""}
                  </span>
                ) : null}
                {item.pickup ? (
                  <span>
                    上门取件 {pickupStatusLabel(item.pickup.status)} ·{" "}
                    {item.pickup.scheduled_date} {item.pickup.time_window}
                  </span>
                ) : null}
                {item.replacement_order_no ? (
                  <span>换货订单 {item.replacement_order_no}</span>
                ) : null}
                {item.exchange_supplement_cents > 0 ? (
                  <span
                    data-testid={`admin-exchange-supplement-${item.id}`}
                  >
                    补差价 {formatCents(item.exchange_supplement_cents)} 元 ·{" "}
                    {item.exchange_payment_status === "paid"
                      ? "已支付"
                      : item.exchange_payment_status === "cancelled"
                        ? "已取消"
                        : "待支付"}
                    {item.exchange_payment_ref
                      ? ` · ${item.exchange_payment_ref}`
                      : ""}
                  </span>
                ) : null}
                {item.exchange_payment_status === "pending" &&
                item.exchange_payment_deadline ? (
                  <span data-testid={`admin-exchange-deadline-${item.id}`}>
                    超时自动取消{" "}
                    {formatDateTime(item.exchange_payment_deadline)}
                  </span>
                ) : null}
                {item.exchange_payment_status === "cancelled" ? (
                  <span data-testid={`admin-exchange-cancelled-${item.id}`}>
                    取消原因 {item.exchange_cancel_reason}
                    {item.refund_proposal_id
                      ? ` · 退款提案 #${item.refund_proposal_id}`
                      : " · 无退款"}
                  </span>
                ) : null}
                {item.replacement_shipments.map((shipment) => (
                  <span
                    key={`replacement-shipment-${item.id}-${shipment.id}`}
                    data-testid={`admin-replacement-shipment-${item.id}-${shipment.id}`}
                  >
                    补发包裹 #{shipment.package_no} · {shipment.carrier}{" "}
                    {shipment.tracking_no}
                    {shipment.current_node
                      ? ` · ${shipment.current_node}`
                      : ""}
                  </span>
                ))}
                {item.tracking_events.length > 0 ? (
                  <span>
                    回寄物流{" "}
                    {RETURN_TRACKING_LABELS[item.shipment_status] ??
                      item.shipment_status}
                  </span>
                ) : null}
              </div>
              {item.exchange_payment_status === "pending" ? (
                <div className="proposal-actions">
                  <button
                    className="btn btn-ghost btn-sm"
                    type="button"
                    data-testid={`admin-extend-exchange-${item.id}`}
                    disabled={
                      exchangeExtendBusyId === item.id || role !== "owner"
                    }
                    onClick={() => void extendExchangeSupplement(item)}
                  >
                    延期 24 小时
                  </button>
                  <button
                    className="btn btn-ghost btn-sm"
                    type="button"
                    data-testid={`admin-cancel-exchange-${item.id}`}
                    disabled={
                      exchangeCancelBusyId === item.id || role !== "owner"
                    }
                    onClick={() => void cancelExchangeByStaff(item)}
                  >
                    取消换货并生成退款提案
                  </button>
                  {role !== "owner" ? (
                    <span className="muted">取消换货需要店主权限</span>
                  ) : null}
                </div>
              ) : null}
              {item.appeal ? (
                <div
                  className="appeal-panel"
                  data-testid={`admin-return-appeal-${item.id}`}
                >
                  <p className="after-sales-reason">
                    扣减申诉原因：{item.appeal.reason}
                  </p>
                  {item.appeal.attachment_ids.length > 0 ? (
                    <div className="attachment-grid">
                      {item.appeal.attachment_ids.map((attachmentId) => (
                        <a
                          key={`admin-appeal-attachment-${item.appeal!.id}-${attachmentId}`}
                          href={`/api/store/attachments/${attachmentId}`}
                          target="_blank"
                          rel="noreferrer"
                        >
                          <img
                            src={`/api/store/attachments/${attachmentId}`}
                            alt="申诉凭证"
                          />
                        </a>
                      ))}
                    </div>
                  ) : null}
                  {item.appeal.resolution_note ? (
                    <p className="muted">
                      处理说明：{item.appeal.resolution_note}
                    </p>
                  ) : null}
                  {item.appeal.status === "pending" && role === "owner" ? (
                    <div className="appeal-actions">
                      <label className="muted">
                        核准金额（元）
                        <input
                          type="number"
                          min={0.01}
                          step="0.01"
                          data-testid={`appeal-amount-${item.id}`}
                          value={
                            appealAmounts[item.id] ??
                            (item.appeal.requested_cents / 100).toFixed(2)
                          }
                          onChange={(event) =>
                            setAppealAmounts((previous) => ({
                              ...previous,
                              [item.id]: event.target.value,
                            }))
                          }
                        />
                      </label>
                      <textarea
                        value={appealNotes[item.id] ?? ""}
                        maxLength={1000}
                        placeholder="处理说明（驳回必填）"
                        data-testid={`appeal-note-${item.id}`}
                        onChange={(event) =>
                          setAppealNotes((previous) => ({
                            ...previous,
                            [item.id]: event.target.value,
                          }))
                        }
                      />
                      <div className="proposal-actions">
                        <button
                          className="btn btn-primary btn-sm"
                          type="button"
                          data-testid={`accept-appeal-${item.id}`}
                          disabled={appealBusyId === item.id}
                          onClick={() => void resolveAppeal(item, true)}
                        >
                          支持申诉并生成退款提案
                        </button>
                        <button
                          className="btn btn-ghost btn-sm"
                          type="button"
                          data-testid={`reject-appeal-${item.id}`}
                          disabled={appealBusyId === item.id}
                          onClick={() => void resolveAppeal(item, false)}
                        >
                          驳回申诉
                        </button>
                      </div>
                    </div>
                  ) : null}
                  {item.appeal.status === "pending" && role !== "owner" ? (
                    <p className="muted">
                      客服账号只能查看申诉，处理需要店主权限。
                    </p>
                  ) : null}
                </div>
              ) : null}
              {item.pickup ? (
                <div
                  className="pickup-panel"
                  data-testid={`admin-return-pickup-${item.id}`}
                >
                  <p className="proposal-meta">
                    <span>上门取件</span>
                    <StatusBadge
                      label={pickupStatusLabel(item.pickup.status)}
                      tone={pickupTone(item.pickup.status)}
                    />
                    <span>
                      {item.pickup.scheduled_date} {item.pickup.time_window}
                    </span>
                    <span>取件单 {item.pickup.pickup_no}</span>
                  </p>
                  <p className="muted">
                    取件地址：{item.pickup.address_region}{" "}
                    {item.pickup.address_city}{" "}
                    {item.pickup.address_detail} ·{" "}
                    {item.pickup.address_recipient}{" "}
                    {item.pickup.address_phone}
                  </p>
                  {item.pickup.cancel_reason ? (
                    <p className="muted">
                      取消原因：{item.pickup.cancel_reason}
                    </p>
                  ) : null}
                  {item.pickup.status === "scheduled" && role === "owner" ? (
                    <div className="appeal-actions">
                      <button
                        className="btn btn-primary btn-sm"
                        type="button"
                        data-testid={`pickup-picked-up-${item.id}`}
                        disabled={pickupBusyId === item.id}
                        onClick={() => void markPickupPickedUp(item)}
                      >
                        标记已揽收
                      </button>
                      <label className="muted">
                        取消说明
                        <input
                          value={pickupNotes[item.id] ?? ""}
                          maxLength={1000}
                          placeholder="取消取件原因"
                          data-testid={`pickup-cancel-note-${item.id}`}
                          onChange={(event) =>
                            setPickupNotes((previous) => ({
                              ...previous,
                              [item.id]: event.target.value,
                            }))
                          }
                        />
                      </label>
                      <button
                        className="btn btn-ghost btn-sm"
                        type="button"
                        data-testid={`pickup-cancel-${item.id}`}
                        disabled={pickupBusyId === item.id}
                        onClick={() => void cancelPickupByStaff(item)}
                      >
                        取消取件预约
                      </button>
                    </div>
                  ) : null}
                  {item.pickup.status === "scheduled" && role !== "owner" ? (
                    <p className="muted">
                      客服账号只能查看取件预约，揽收操作需要店主权限。
                    </p>
                  ) : null}
                </div>
              ) : null}
              {item.tracking_events.length > 0 ? (
                <ol
                  className="shipment-timeline return-tracking-timeline"
                  data-testid={`return-tracking-events-${item.id}`}
                >
                  {item.tracking_events.map((event) => (
                    <li key={`${event.status}-${event.happened_at}`}>
                      <span className="timeline-node">{event.label}</span>
                      <span className="timeline-desc">
                        {event.description}
                      </span>
                      <time>{formatDateTime(event.happened_at)}</time>
                    </li>
                  ))}
                </ol>
              ) : null}
              {item.replacement_shipments.map((shipment) =>
                shipment.events.length > 0 ? (
                  <ol
                    key={`replacement-events-${item.id}-${shipment.id}`}
                    className="shipment-timeline return-tracking-timeline"
                    data-testid={`admin-replacement-tracking-${item.id}-${shipment.id}`}
                  >
                    {shipment.events.map((event) => (
                      <li key={`${event.node}-${event.happened_at}`}>
                        <span className="timeline-node">{event.node}</span>
                        <span className="timeline-desc">
                          {event.description}
                        </span>
                        <time>{formatDateTime(event.happened_at)}</time>
                      </li>
                    ))}
                  </ol>
                ) : null,
              )}
              {item.items.length > 0 ? (
                <div
                  className="proposal-meta"
                  data-testid={`admin-return-items-${item.id}`}
                >
                  {item.items.map((line) => (
                    <span
                      key={line.sku}
                      data-testid={`admin-return-item-${item.id}-${line.sku}`}
                    >
                      {line.title} · {line.sku} × {line.qty}
                      {line.exchange_sku
                        ? ` → 换 ${line.exchange_sku}`
                        : ""}
                      {line.disposition === "restock"
                        ? " · 已回补"
                        : line.disposition === "damaged"
                          ? " · 已报损"
                          : ""}
                      {line.responsibility
                        ? ` · ${RESPONSIBILITY_LABELS[line.responsibility] ?? line.responsibility}`
                        : ""}
                    </span>
                  ))}
                </div>
              ) : null}
              {item.inspection_attachment_ids.length > 0 ? (
                <div
                  className="attachment-grid"
                  data-testid={`inspection-images-${item.id}`}
                >
                  {item.inspection_attachment_ids.map((attachmentId) => (
                    <a
                      href={`/api/admin/attachments/${attachmentId}`}
                      target="_blank"
                      rel="noreferrer"
                      key={attachmentId}
                    >
                      <img
                        src={`/api/admin/attachments/${attachmentId}`}
                        alt="质检照片"
                      />
                    </a>
                  ))}
                </div>
              ) : null}
              <p className="after-sales-reason">
                退货原因：{returnReasonLabel(item.reason_category)} ·{" "}
                {item.reason || "—"}
              </p>
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
                        alt="退货凭证"
                      />
                    </a>
                  ))}
                </div>
              ) : null}
              {item.return_address ? (
                <p className="muted">退货地址：{item.return_address}</p>
              ) : null}
              {item.status === "rejected" && item.rejected_reason ? (
                <div className="notice notice-warn">
                  驳回原因：{item.rejected_reason}
                </div>
              ) : null}

              {role === "owner" && item.status === "requested" ? (
                <div className="proposal-actions">
                  <button
                    className="btn btn-primary btn-sm"
                    type="button"
                    data-testid={`approve-return-${item.id}`}
                    disabled={busyId === item.id}
                    onClick={() => void act(item, "approve")}
                  >
                    批准退货
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
                      aria-label={`退货 ${item.id} 驳回原因`}
                    />
                    <button
                      className="btn btn-danger btn-sm"
                      type="button"
                      data-testid={`reject-return-${item.id}`}
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
                  {role === "owner" &&
                  item.tracking_no &&
                  RETURN_TRACKING_NEXT[item.shipment_status] ? (
                    <button
                      className="btn btn-ghost btn-sm"
                      type="button"
                      data-testid={`advance-return-tracking-${item.id}`}
                      disabled={busyId === item.id}
                      onClick={() => void advanceTracking(item)}
                    >
                      推进物流：
                      {
                        RETURN_TRACKING_LABELS[
                          RETURN_TRACKING_NEXT[item.shipment_status]
                        ]
                      }
                    </button>
                  ) : null}
                  {role === "owner" &&
                  item.inspection_attachment_ids.length < 3 ? (
                    <label
                      className="btn btn-ghost btn-sm"
                      style={{ cursor: "pointer" }}
                    >
                      质检照片
                      <input
                        type="file"
                        hidden
                        multiple
                        accept="image/png,image/jpeg,image/webp"
                        data-testid={`inspection-files-${item.id}`}
                        onChange={(event) => {
                          const files = Array.from(
                            event.target.files ?? [],
                          );
                          event.target.value = "";
                          if (files.length > 0) {
                            void uploadInspectionPhotos(item, files);
                          }
                        }}
                      />
                    </label>
                  ) : null}
                  {role === "owner" && item.tracking_no ? (
                    <>
                      <input
                        value={exceptionReasons[item.id] ?? ""}
                        onChange={(event) =>
                          setExceptionReasons((previous) => ({
                            ...previous,
                            [item.id]: event.target.value,
                          }))
                        }
                        placeholder="物流异常说明"
                        maxLength={500}
                        data-testid={`return-exception-${item.id}`}
                      />
                      <button
                        className="btn btn-ghost btn-sm"
                        type="button"
                        data-testid={`report-return-exception-${item.id}`}
                        disabled={
                          busyId === item.id ||
                          !(exceptionReasons[item.id] ?? "").trim()
                        }
                        onClick={() =>
                          void reportTrackingException(item)
                        }
                      >
                        登记物流异常
                      </button>
                    </>
                  ) : null}
                  {item.items.length > 0 ? (
                    <div
                      className="return-dispositions"
                      data-testid={`dispositions-return-${item.id}`}
                    >
                      {item.items.map((line) => (
                        <label
                          className="return-disposition-row"
                          key={line.id}
                        >
                          <span>
                            {line.title} × {line.qty}
                          </span>
                          <select
                            value={
                              dispositions[item.id]?.[line.id] ?? "restock"
                            }
                            data-testid={`disposition-${item.id}-${line.id}`}
                            onChange={(event) =>
                              setDispositions((previous) => ({
                                ...previous,
                                [item.id]: {
                                  ...(previous[item.id] ?? {}),
                                  [line.id]: event.target.value as
                                    | "restock"
                                    | "damaged",
                                },
                              }))
                            }
                          >
                            <option value="restock">回补库存</option>
                            <option value="damaged">报损不入库</option>
                          </select>
                          <select
                            value={
                              responsibilities[item.id]?.[line.id] ?? ""
                            }
                            aria-label={`${line.sku} 责任判定`}
                            data-testid={`responsibility-${item.id}-${line.id}`}
                            onChange={(event) =>
                              setResponsibilities((previous) => ({
                                ...previous,
                                [item.id]: {
                                  ...(previous[item.id] ?? {}),
                                  [line.id]: event.target.value,
                                },
                              }))
                            }
                          >
                            <option value="">责任未判定</option>
                            <option value="merchant">商家责任</option>
                            <option value="customer">顾客责任</option>
                            <option value="logistics">物流责任</option>
                            <option value="other">其他责任</option>
                          </select>
                          {item.resolution === "refund" ? (
                            <input
                              type="number"
                              min={0}
                              step="0.01"
                              style={{ width: 110 }}
                              placeholder="扣减(元)"
                              aria-label={`${line.sku} 质检扣减`}
                              data-testid={`item-deduction-${item.id}-${line.id}`}
                              value={
                                itemDeductions[item.id]?.[line.id] ?? ""
                              }
                              onChange={(event) =>
                                setItemDeductions((previous) => ({
                                  ...previous,
                                  [item.id]: {
                                    ...(previous[item.id] ?? {}),
                                    [line.id]: event.target.value,
                                  },
                                }))
                              }
                            />
                          ) : null}
                        </label>
                      ))}
                    </div>
                  ) : (
                    <label className="muted">
                      <input
                        type="checkbox"
                        data-testid={`restock-return-${item.id}`}
                        checked={restockFlags[item.id] ?? true}
                        onChange={(event) =>
                          setRestockFlags((previous) => ({
                            ...previous,
                            [item.id]: event.target.checked,
                          }))
                        }
                      />{" "}
                      按原发货仓回补库存
                    </label>
                  )}
                  {item.resolution === "refund" ? (
                    <div className="inspection-adjust">
                      {item.items.length === 0 ? (
                        <div className="field">
                          <label
                            htmlFor={`inspection-deduction-${item.id}`}
                          >
                            质检扣减（元）
                          </label>
                          <input
                            id={`inspection-deduction-${item.id}`}
                            data-testid={`inspection-deduction-${item.id}`}
                            type="number"
                            min={0}
                            step="0.01"
                            placeholder="0.00"
                            value={deductions[item.id] ?? ""}
                            onChange={(event) =>
                              setDeductions((previous) => ({
                                ...previous,
                                [item.id]: event.target.value,
                              }))
                            }
                          />
                        </div>
                      ) : null}
                      <div className="field">
                        <label
                          htmlFor={`inspection-note-${item.id}`}
                        >
                          扣减原因（有扣减时必填）
                        </label>
                        <input
                          id={`inspection-note-${item.id}`}
                          data-testid={`inspection-note-${item.id}`}
                          maxLength={1000}
                          placeholder="如：缺少原装配件、外壳划伤"
                          value={inspectionNotes[item.id] ?? ""}
                          onChange={(event) =>
                            setInspectionNotes((previous) => ({
                              ...previous,
                              [item.id]: event.target.value,
                            }))
                          }
                        />
                      </div>
                    </div>
                  ) : null}
                  <button
                    className="btn btn-primary btn-sm"
                    type="button"
                    data-testid={`receive-return-${item.id}`}
                    disabled={busyId === item.id}
                    onClick={() => void act(item, "receive")}
                  >
                    确认收货并生成退款提案
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
