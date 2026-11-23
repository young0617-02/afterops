"use client";

import Link from "next/link";
import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type FormEvent,
} from "react";

import { StatusBadge } from "@/components/StatusBadge";
import { useCustomer } from "@/components/CustomerProvider";
import { api, errorMessage } from "@/lib/api";
import {
  actionTypeLabel,
  formatDateTime,
  handoffIntentLabel,
  handoffQueueLabel,
  handoffStatusLabel,
  proposalStatusLabel,
  repairQuoteStatusLabel,
  repairStatusLabel,
  returnReasonLabel,
  returnStatusLabel,
} from "@/lib/status";
import type {
  AfterSalesItem,
  AfterSalesTimelineEvent,
  CustomerHandoff,
  OrderDetail,
  OrderSummary,
  ProductSummary,
  RepairEligibility,
  RepairView,
  ReturnEligibility,
  ReturnRequestView,
} from "@/lib/types";

function formatCents(value: number): string {
  return (value / 100).toFixed(2);
}

function statusHint(item: AfterSalesItem): string {
  if (item.status === "pending") return "等待店主审核";
  if (item.status === "approved") return "已批准，执行中";
  if (item.status === "executing") return "执行中";
  if (item.status === "executed") {
    if (item.action_type === "refund" || item.action_type === "compensation") {
      return "款项已执行";
    }
    if (item.action_type === "address_change") return "地址已更新";
    return "已执行";
  }
  if (item.status === "rejected") return "申请未通过";
  if (item.status === "failed") return "执行失败，请联系人工";
  if (item.status === "withdrawn") return "申请已撤回";
  if (item.status === "expired") return "申请已过期";
  return item.status;
}

function handoffTone(status: string): string {
  if (status === "open") return "badge-warn";
  if (status === "claimed") return "badge-neutral";
  return "badge-ok";
}

function returnTone(status: string): string {
  if (status === "received") return "badge-ok";
  if (
    status === "rejected" ||
    status === "withdrawn" ||
    status === "exchange_cancelled"
  ) {
    return "badge-muted";
  }
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

function timelineKindLabel(kind: string): string {
  const labels: Record<string, string> = {
    return: "退货",
    return_tracking: "退货物流",
    return_pickup: "上门取件",
    return_appeal: "扣减申诉",
    repair: "维修",
    repair_quote: "维修报价",
    proposal: "售后提案",
    refund: "退款",
    compensation: "补偿",
    invoice: "发票",
    handoff: "人工工单",
  };
  return labels[kind] ?? kind;
}

function timelineTone(kind: string): string {
  if (kind === "refund" || kind === "handoff") return "badge-ok";
  if (kind === "return_appeal" || kind === "invoice") return "badge-warn";
  if (kind === "repair_quote") return "badge-warn";
  return "badge-neutral";
}

function pickupDate(daysAhead: number): string {
  const target = new Date();
  target.setDate(target.getDate() + daysAhead);
  const month = String(target.getMonth() + 1).padStart(2, "0");
  const day = String(target.getDate()).padStart(2, "0");
  return `${target.getFullYear()}-${month}-${day}`;
}

function defaultPickupForm() {
  return { scheduled_date: pickupDate(1), time_window: "09:00-12:00" };
}

function repairTone(status: string): string {
  if (status === "completed") return "badge-ok";
  if (status === "rejected" || status === "withdrawn") return "badge-muted";
  return "badge-warn";
}

function repairQuoteTone(status: string): string {
  if (status === "paid") return "badge-ok";
  if (status === "declined") return "badge-muted";
  return "badge-warn";
}

function returnResponsibilityLabel(value: string): string {
  const labels: Record<string, string> = {
    merchant: "商家责任",
    customer: "顾客责任",
    logistics: "物流责任",
    other: "其他责任",
  };
  return labels[value] ?? value;
}

export default function AfterSalesPage() {
  const { customer, refresh } = useCustomer();
  const [items, setItems] = useState<AfterSalesItem[]>([]);
  const [timeline, setTimeline] = useState<AfterSalesTimelineEvent[]>([]);
  const [tickets, setTickets] = useState<CustomerHandoff[]>([]);
  const [returns, setReturns] = useState<ReturnRequestView[]>([]);
  const [repairs, setRepairs] = useState<RepairView[]>([]);
  const [orders, setOrders] = useState<OrderSummary[]>([]);
  const [returnOrderNo, setReturnOrderNo] = useState("");
  const [returnReason, setReturnReason] = useState("");
  const [returnReasonCategory, setReturnReasonCategory] = useState("other");
  const [returnResolution, setReturnResolution] = useState<
    "refund" | "exchange"
  >("refund");
  const [exchangeSkus, setExchangeSkus] = useState<Record<string, string>>(
    {},
  );
  const [exchangeProducts, setExchangeProducts] = useState<ProductSummary[]>(
    [],
  );
  const [returnFiles, setReturnFiles] = useState<File[]>([]);
  const [returnEvidenceFiles, setReturnEvidenceFiles] = useState<
    Record<number, File[]>
  >({});
  const [appealReasons, setAppealReasons] = useState<Record<number, string>>(
    {},
  );
  const [appealFiles, setAppealFiles] = useState<Record<number, File[]>>({});
  const [appealBusyId, setAppealBusyId] = useState<number | null>(null);
  const [pickupForms, setPickupForms] = useState<
    Record<number, { scheduled_date: string; time_window: string }>
  >({});
  const [pickupBusyId, setPickupBusyId] = useState<number | null>(null);
  const [supplementBusyId, setSupplementBusyId] = useState<number | null>(
    null,
  );
  const [exchangeCancelBusyId, setExchangeCancelBusyId] = useState<
    number | null
  >(null);
  const [returnBusy, setReturnBusy] = useState(false);
  const [returnEligibility, setReturnEligibility] =
    useState<ReturnEligibility | null>(null);
  const [eligibilityBusy, setEligibilityBusy] = useState(false);
  const [returnDetail, setReturnDetail] = useState<OrderDetail | null>(null);
  const [returnDetailBusy, setReturnDetailBusy] = useState(false);
  const [selectedReturnSkus, setSelectedReturnSkus] = useState<string[]>([]);
  const [repairOrderNo, setRepairOrderNo] = useState("");
  const [repairSku, setRepairSku] = useState("");
  const [repairIssue, setRepairIssue] = useState("");
  const [repairFiles, setRepairFiles] = useState<File[]>([]);
  const [repairDetail, setRepairDetail] = useState<OrderDetail | null>(null);
  const [repairDetailBusy, setRepairDetailBusy] = useState(false);
  const [repairEligibility, setRepairEligibility] =
    useState<RepairEligibility | null>(null);
  const [repairEligibilityBusy, setRepairEligibilityBusy] = useState(false);
  const [repairBusy, setRepairBusy] = useState(false);
  const [repairBusyId, setRepairBusyId] = useState<number | null>(null);
  const [repairTracking, setRepairTracking] = useState<
    Record<number, { carrier: string; tracking_no: string }>
  >({});
  const eligibilitySeq = useRef(0);
  const repairEligibilitySeq = useRef(0);
  const [proposalFiles, setProposalFiles] = useState<Record<number, File[]>>(
    {},
  );
  const [trackingForms, setTrackingForms] = useState<
    Record<number, { carrier: string; tracking_no: string }>
  >({});
  const [loading, setLoading] = useState(false);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [notifyEmail, setNotifyEmail] = useState("");
  const [notifyShipment, setNotifyShipment] = useState(true);
  const [notifyAfterSales, setNotifyAfterSales] = useState(true);
  const [notifyReturn, setNotifyReturn] = useState(true);
  const [profileBusy, setProfileBusy] = useState(false);
  const [ratingScores, setRatingScores] = useState<Record<number, number>>({});
  const [ratingComments, setRatingComments] = useState<
    Record<number, string>
  >({});
  const [ratingBusyId, setRatingBusyId] = useState<number | null>(null);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    setNotifyEmail(customer?.email ?? "");
    setNotifyShipment(customer?.notify_email_shipment ?? true);
    setNotifyAfterSales(customer?.notify_email_after_sales ?? true);
    setNotifyReturn(customer?.notify_email_return ?? true);
  }, [customer]);

  const load = useCallback(async () => {
    if (!customer) {
      setItems([]);
      setTimeline([]);
      setTickets([]);
      setReturns([]);
      setRepairs([]);
      setOrders([]);
      return;
    }
    setLoading(true);
    try {
      const [
        afterSales,
        timelineRows,
        handoffs,
        returnRows,
        repairRows,
        orderRows,
        productRows,
      ] = await Promise.all([
        api<AfterSalesItem[]>("/api/store/after-sales"),
        api<AfterSalesTimelineEvent[]>(
          "/api/store/after-sales/timeline?limit=30",
        ),
        api<CustomerHandoff[]>("/api/store/handoffs"),
        api<ReturnRequestView[]>("/api/store/returns"),
        api<RepairView[]>("/api/store/repairs"),
        api<OrderSummary[]>("/api/store/orders"),
        api<ProductSummary[]>("/api/store/products?limit=100"),
      ]);
      setItems(afterSales);
      setTimeline(timelineRows);
      setTickets(handoffs);
      setReturns(returnRows);
      setRepairs(repairRows);
      setOrders(orderRows);
      setExchangeProducts(productRows.filter((row) => row.in_stock));
      const eligible = orderRows.filter((row) =>
        ["delivered", "completed"].includes(row.status),
      );
      setReturnOrderNo((previous) =>
        eligible.some((row) => row.order_no === previous)
          ? previous
          : eligible[0]?.order_no ?? "",
      );
      setRepairOrderNo((previous) =>
        eligible.some((row) => row.order_no === previous)
          ? previous
          : eligible[0]?.order_no ?? "",
      );
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

  const loadEligibility = useCallback(
    async (orderNo: string, resolution: "refund" | "exchange") => {
      const seq = ++eligibilitySeq.current;
      if (!orderNo) {
        setReturnEligibility(null);
        return;
      }
      setEligibilityBusy(true);
      try {
        const body = await api<ReturnEligibility>(
          `/api/store/returns/eligibility?order_no=${encodeURIComponent(orderNo)}&resolution=${resolution}`,
        );
        if (seq === eligibilitySeq.current) {
          setReturnEligibility(body);
        }
      } catch {
        if (seq === eligibilitySeq.current) {
          setReturnEligibility(null);
        }
      } finally {
        if (seq === eligibilitySeq.current) {
          setEligibilityBusy(false);
        }
      }
    },
    [],
  );

  useEffect(() => {
    if (!customer || !returnOrderNo) {
      setReturnEligibility(null);
      return;
    }
    void loadEligibility(returnOrderNo, returnResolution);
  }, [customer, returnOrderNo, returnResolution, loadEligibility]);

  useEffect(() => {
    if (!customer || !returnOrderNo) {
      setReturnDetail(null);
      setSelectedReturnSkus([]);
      return;
    }
    let cancelled = false;
    setReturnDetailBusy(true);
    api<OrderDetail>(
      `/api/store/orders/${encodeURIComponent(returnOrderNo)}`,
    )
      .then((detail) => {
        if (cancelled) return;
        setReturnDetail(detail);
        setSelectedReturnSkus(detail.items.map((item) => item.sku));
      })
      .catch(() => {
        if (!cancelled) {
          setReturnDetail(null);
          setSelectedReturnSkus([]);
        }
      })
      .finally(() => {
        if (!cancelled) setReturnDetailBusy(false);
      });
    return () => {
      cancelled = true;
    };
  }, [customer, returnOrderNo]);

  useEffect(() => {
    if (!customer || !repairOrderNo) {
      setRepairDetail(null);
      setRepairSku("");
      return;
    }
    let cancelled = false;
    setRepairDetailBusy(true);
    api<OrderDetail>(
      `/api/store/orders/${encodeURIComponent(repairOrderNo)}`,
    )
      .then((detail) => {
        if (cancelled) return;
        setRepairDetail(detail);
        setRepairSku((previous) =>
          detail.items.some((item) => item.sku === previous)
            ? previous
            : detail.items[0]?.sku ?? "",
        );
      })
      .catch(() => {
        if (!cancelled) {
          setRepairDetail(null);
          setRepairSku("");
        }
      })
      .finally(() => {
        if (!cancelled) setRepairDetailBusy(false);
      });
    return () => {
      cancelled = true;
    };
  }, [customer, repairOrderNo]);

  const loadRepairEligibility = useCallback(async (orderNo: string) => {
    const seq = ++repairEligibilitySeq.current;
    if (!orderNo) {
      setRepairEligibility(null);
      return;
    }
    setRepairEligibilityBusy(true);
    try {
      const body = await api<RepairEligibility>(
        `/api/store/orders/${encodeURIComponent(orderNo)}/repair-eligibility`,
      );
      if (seq === repairEligibilitySeq.current) {
        setRepairEligibility(body);
      }
    } catch {
      if (seq === repairEligibilitySeq.current) {
        setRepairEligibility(null);
      }
    } finally {
      if (seq === repairEligibilitySeq.current) {
        setRepairEligibilityBusy(false);
      }
    }
  }, []);

  useEffect(() => {
    if (!customer || !repairOrderNo) {
      setRepairEligibility(null);
      return;
    }
    void loadRepairEligibility(repairOrderNo);
  }, [customer, repairOrderNo, loadRepairEligibility]);

  useEffect(() => {
    if (!returnEligibility?.items.length) return;
    const eligible = new Set(
      returnEligibility.items
        .filter((item) => item.eligible)
        .map((item) => item.sku),
    );
    setSelectedReturnSkus((previous) => {
      const kept = previous.filter((sku) => eligible.has(sku));
      return kept.length > 0 ? kept : Array.from(eligible);
    });
  }, [returnEligibility]);

  const selectedReturnItems = returnDetail
    ? returnDetail.items.filter((item) =>
        selectedReturnSkus.includes(item.sku),
      )
    : [];

  const deliveredOrders = orders.filter((row) =>
    ["delivered", "completed"].includes(row.status),
  );

  const saveEmail = async (value: string) => {
    setProfileBusy(true);
    setError("");
    setNotice("");
    try {
      await api("/api/store/me", {
        method: "PATCH",
        body: JSON.stringify({
          email: value.trim(),
          notify_email_shipment: notifyShipment,
          notify_email_after_sales: notifyAfterSales,
          notify_email_return: notifyReturn,
        }),
      });
      setNotice(value.trim() ? "通知邮箱已保存" : "通知邮箱已停用");
      await refresh();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setProfileBusy(false);
    }
  };

  const submitRating = async (ticket: CustomerHandoff) => {
    const score = ratingScores[ticket.id];
    if (!score) {
      setError("请选择 1–5 星");
      return;
    }
    setRatingBusyId(ticket.id);
    setError("");
    setNotice("");
    try {
      const result = await api<{
        follow_up_ticket_ref: string | null;
      }>(`/api/store/handoffs/${ticket.id}/rating`, {
        method: "POST",
        body: JSON.stringify({
          score,
          comment: ratingComments[ticket.id] ?? "",
        }),
      });
      setNotice(
        `评价已提交：${score} 星` +
          (result.follow_up_ticket_ref
            ? `，已安排回访工单 ${result.follow_up_ticket_ref}`
            : ""),
      );
      await load();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setRatingBusyId(null);
    }
  };

  const uploadFiles = async (files: File[]) => {
    const ids: number[] = [];
    for (const file of files.slice(0, 3)) {
      const form = new FormData();
      form.append("file", file);
      const uploaded = await api<{ id: number }>("/api/store/attachments", {
        method: "POST",
        body: form,
      });
      ids.push(uploaded.id);
    }
    return ids;
  };

  const createReturn = async (event: FormEvent) => {
    event.preventDefault();
    if (!returnOrderNo || !returnReason.trim()) return;
    setReturnBusy(true);
    setError("");
    setNotice("");
    try {
      const attachmentIds = await uploadFiles(returnFiles);
      await api<ReturnRequestView>("/api/store/returns", {
        method: "POST",
        body: JSON.stringify({
          order_no: returnOrderNo,
          reason: returnReason.trim(),
          reason_category: returnReasonCategory,
          attachment_ids: attachmentIds,
          resolution: returnResolution,
          items: returnDetail
            ? selectedReturnItems.map((item) => ({
                sku: item.sku,
                qty: item.qty,
                exchange_sku:
                  returnResolution === "exchange"
                    ? exchangeSkus[item.sku]?.trim().toUpperCase() || undefined
                    : undefined,
              }))
            : undefined,
        }),
      });
      setNotice("退货申请已提交，等待店主审核");
      setReturnReason("");
      setReturnReasonCategory("other");
      setReturnFiles([]);
      setReturnResolution("refund");
      setExchangeSkus({});
      await load();
      await loadEligibility(returnOrderNo, "refund");
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setReturnBusy(false);
    }
  };

  const submitReturnAppeal = async (row: ReturnRequestView) => {
    const reason = (appealReasons[row.id] ?? "").trim();
    if (!reason) return;
    setAppealBusyId(row.id);
    setError("");
    setNotice("");
    try {
      const attachmentIds = await uploadFiles(appealFiles[row.id] ?? []);
      await api<ReturnRequestView>(`/api/store/returns/${row.id}/appeal`, {
        method: "POST",
        body: JSON.stringify({
          reason,
          attachment_ids: attachmentIds,
        }),
      });
      setNotice(`退货 #${row.id} 扣减申诉已提交，等待店主处理`);
      setAppealReasons((previous) => ({ ...previous, [row.id]: "" }));
      setAppealFiles((previous) => ({ ...previous, [row.id]: [] }));
      await load();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setAppealBusyId(null);
    }
  };

  const withdrawReturnAppeal = async (row: ReturnRequestView) => {
    setAppealBusyId(row.id);
    setError("");
    setNotice("");
    try {
      await api<ReturnRequestView>(
        `/api/store/returns/${row.id}/appeal/withdraw`,
        { method: "POST" },
      );
      setNotice(`退货 #${row.id} 扣减申诉已撤回`);
      await load();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setAppealBusyId(null);
    }
  };

  const scheduleReturnPickup = async (row: ReturnRequestView) => {
    const form = pickupForms[row.id] ?? defaultPickupForm();
    setPickupBusyId(row.id);
    setError("");
    setNotice("");
    try {
      await api<ReturnRequestView>(`/api/store/returns/${row.id}/pickup`, {
        method: "POST",
        body: JSON.stringify({
          scheduled_date: form.scheduled_date,
          time_window: form.time_window,
        }),
      });
      setNotice(`退货 #${row.id} 上门取件已预约`);
      await load();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setPickupBusyId(null);
    }
  };

  const cancelReturnPickup = async (row: ReturnRequestView) => {
    setPickupBusyId(row.id);
    setError("");
    setNotice("");
    try {
      await api<ReturnRequestView>(
        `/api/store/returns/${row.id}/pickup/cancel`,
        { method: "POST" },
      );
      setNotice(`退货 #${row.id} 上门取件预约已取消`);
      await load();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setPickupBusyId(null);
    }
  };

  const payExchangeSupplement = async (row: ReturnRequestView) => {
    setSupplementBusyId(row.id);
    setError("");
    setNotice("");
    try {
      await api<ReturnRequestView>(
        `/api/store/returns/${row.id}/exchange-supplement/pay`,
        { method: "POST" },
      );
      setNotice(`退货 #${row.id} 换货补差价已支付`);
      await load();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setSupplementBusyId(null);
    }
  };

  const cancelExchange = async (row: ReturnRequestView) => {
    if (
      !window.confirm(
        `放弃换货 #${row.id}？换货订单将取消，并按退货规则生成退款提案。`,
      )
    ) {
      return;
    }
    setExchangeCancelBusyId(row.id);
    setError("");
    setNotice("");
    try {
      const result = await api<ReturnRequestView>(
        `/api/store/returns/${row.id}/exchange/cancel`,
        {
          method: "POST",
          body: JSON.stringify({ reason: "顾客放弃补差换货" }),
        },
      );
      setNotice(
        result.refund_proposal_id
          ? `退货 #${row.id} 换货已取消，退款提案 #${result.refund_proposal_id} 待商家审批`
          : `退货 #${row.id} 换货已取消，无需退款`,
      );
      await load();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setExchangeCancelBusyId(null);
    }
  };

  const createRepair = async (event: FormEvent) => {
    event.preventDefault();
    if (!repairOrderNo || !repairSku || !repairIssue.trim()) return;
    setRepairBusy(true);
    setError("");
    setNotice("");
    try {
      const attachmentIds = await uploadFiles(repairFiles);
      await api("/api/store/repairs", {
        method: "POST",
        body: JSON.stringify({
          order_no: repairOrderNo,
          sku: repairSku,
          issue: repairIssue.trim(),
          attachment_ids: attachmentIds,
        }),
      });
      setNotice("维修申请已提交，等待店主审核");
      setRepairIssue("");
      setRepairFiles([]);
      await load();
      await loadRepairEligibility(repairOrderNo);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setRepairBusy(false);
    }
  };

  const withdrawRepair = async (repairId: number) => {
    if (!window.confirm(`撤回维修申请 #${repairId}？`)) return;
    setRepairBusyId(repairId);
    setError("");
    setNotice("");
    try {
      await api(`/api/store/repairs/${repairId}/withdraw`, {
        method: "POST",
      });
      setNotice(`维修申请 #${repairId} 已撤回`);
      await load();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setRepairBusyId(null);
    }
  };

  const submitRepairTracking = async (repair: RepairView) => {
    const form = repairTracking[repair.id] ?? {
      carrier: "",
      tracking_no: "",
    };
    if (!form.carrier.trim() || !form.tracking_no.trim()) {
      setError("请填写承运商与运单号");
      return;
    }
    setRepairBusyId(repair.id);
    setError("");
    setNotice("");
    try {
      await api(`/api/store/repairs/${repair.id}/tracking`, {
        method: "POST",
        body: JSON.stringify({
          carrier: form.carrier.trim(),
          tracking_no: form.tracking_no.trim(),
        }),
      });
      setNotice(`维修 #${repair.id} 已登记回寄`);
      await load();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setRepairBusyId(null);
    }
  };

  const confirmRepairReceived = async (repair: RepairView) => {
    setRepairBusyId(repair.id);
    setError("");
    setNotice("");
    try {
      await api(`/api/store/repairs/${repair.id}/confirm-receipt`, {
        method: "POST",
      });
      setNotice(`维修 #${repair.id} 已确认收到，维修完成`);
      await load();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setRepairBusyId(null);
    }
  };

  const actRepairQuote = async (
    repair: RepairView,
    action: "accept" | "decline" | "pay",
  ) => {
    setRepairBusyId(repair.id);
    setError("");
    setNotice("");
    try {
      await api(`/api/store/repairs/${repair.id}/quote/${action}`, {
        method: "POST",
      });
      setNotice(
        action === "accept"
          ? `维修 #${repair.id} 报价已接受，请完成支付`
          : action === "decline"
            ? `维修 #${repair.id} 报价已拒绝，可等待商家调整报价`
            : `维修 #${repair.id} 维修费已支付`,
      );
      await load();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setRepairBusyId(null);
    }
  };

  const trackingForm = (returnId: number) =>
    trackingForms[returnId] ?? { carrier: "", tracking_no: "" };

  const withdrawReturn = async (returnId: number) => {
    if (!window.confirm(`撤回退货申请 #${returnId}？`)) return;
    setReturnBusy(true);
    setError("");
    setNotice("");
    try {
      await api(`/api/store/returns/${returnId}/withdraw`, {
        method: "POST",
      });
      setNotice(`退货 #${returnId} 已撤回`);
      await load();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setReturnBusy(false);
    }
  };

  const attachReturnEvidence = async (row: ReturnRequestView) => {
    const files = returnEvidenceFiles[row.id] ?? [];
    const remaining = 3 - row.attachment_ids.length;
    if (files.length === 0 || remaining <= 0) return;
    setReturnBusy(true);
    setError("");
    setNotice("");
    try {
      const ids = await uploadFiles(files.slice(0, remaining));
      await api(`/api/store/returns/${row.id}/attachments`, {
        method: "POST",
        body: JSON.stringify({ attachment_ids: ids }),
      });
      setNotice(`退货 #${row.id} 已补充凭证`);
      setReturnEvidenceFiles((previous) => ({
        ...previous,
        [row.id]: [],
      }));
      await load();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setReturnBusy(false);
    }
  };

  const submitTracking = async (returnId: number) => {
    const form = trackingForm(returnId);
    if (!form.carrier.trim() || !form.tracking_no.trim()) return;
    setReturnBusy(true);
    setError("");
    setNotice("");
    try {
      await api(`/api/store/returns/${returnId}/tracking`, {
        method: "POST",
        body: JSON.stringify({
          carrier: form.carrier,
          tracking_no: form.tracking_no,
        }),
      });
      setNotice(`退货 #${returnId} 已登记回寄单号`);
      await load();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setReturnBusy(false);
    }
  };

  const attachEvidence = async (item: AfterSalesItem) => {
    const files = proposalFiles[item.id] ?? [];
    if (files.length === 0) return;
    setBusyId(item.id);
    setError("");
    setNotice("");
    try {
      const ids = await uploadFiles(files);
      await api(`/api/store/after-sales/${item.id}/attachments`, {
        method: "POST",
        body: JSON.stringify({ attachment_ids: ids }),
      });
      setNotice(`售后申请 #${item.id} 已补充凭证`);
      setProposalFiles((previous) => ({ ...previous, [item.id]: [] }));
      await load();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusyId(null);
    }
  };

  const withdraw = async (item: AfterSalesItem) => {
    setBusyId(item.id);
    setError("");
    setNotice("");
    try {
      await api(`/api/store/after-sales/${item.id}/withdraw`, {
        method: "POST",
      });
      setNotice(`售后申请 #${item.id} 已撤回`);
      await load();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusyId(null);
    }
  };

  if (!customer) {
    return (
      <div className="empty-state">
        请先在右上角用手机号登录，再查看售后进度。
      </div>
    );
  }

  const selectedRepairEligibility =
    repairEligibility?.items.find((item) => item.sku === repairSku) ?? null;

  return (
    <>
      <div className="section-head">
        <div>
          <h2>售后进度</h2>
          <p>退款、补偿、改地址申请与人工工单的处理状态都来自交易系统。</p>
        </div>
        <Link className="btn btn-ghost" href="/orders">
          查看订单
        </Link>
      </div>

      {notice ? <div className="notice notice-success">{notice}</div> : null}
      {error ? <div className="notice notice-error">{error}</div> : null}

      {timeline.length > 0 ? (
        <section
          className="after-sales-section"
          data-testid="customer-after-sales-timeline"
        >
          <h3>售后时间线</h3>
          <ol className="after-sales-timeline">
            {timeline.map((event, index) => (
              <li
                key={`${event.kind}-${event.entity_id}-${event.occurred_at}-${index}`}
                data-testid={`timeline-event-${index}`}
              >
                <div className="timeline-head">
                  <StatusBadge
                    label={timelineKindLabel(event.kind)}
                    tone={timelineTone(event.kind)}
                  />
                  <strong>{event.title}</strong>
                  <time>{formatDateTime(event.occurred_at)}</time>
                </div>
                {event.detail ? (
                  <p className="timeline-detail">{event.detail}</p>
                ) : null}
                {event.order_no ? (
                  <span className="muted">订单 {event.order_no}</span>
                ) : null}
              </li>
            ))}
          </ol>
        </section>
      ) : null}

      <section className="panel panel-pad" style={{ marginBottom: 18 }}>
        <div className="section-head" style={{ marginBottom: 10 }}>
          <div>
            <h3 style={{ margin: 0 }}>通知邮箱</h3>
            <p>用于接收发货、退款、退货与人工工单的邮件通知；留空即停用。</p>
          </div>
          <span
            className={`badge ${customer.email ? "badge-ok" : "badge-neutral"}`}
          >
            {customer.email ? "已开启" : "未设置"}
          </span>
        </div>
        <form
          className="fulfillment-toolbar"
          style={{ marginTop: 0 }}
          onSubmit={(event) => {
            event.preventDefault();
            void saveEmail(notifyEmail);
          }}
        >
          <input
            type="email"
            value={notifyEmail}
            onChange={(event) => setNotifyEmail(event.target.value)}
            placeholder="name@example.com"
            maxLength={255}
            aria-label="通知邮箱"
            data-testid="notify-email"
            style={{ flex: 1 }}
          />
          <button
            className="btn btn-primary btn-sm"
            type="submit"
            disabled={profileBusy}
            data-testid="notify-email-save"
          >
            {profileBusy ? "保存中" : "保存"}
          </button>
          {customer.email ? (
            <button
              className="btn btn-ghost btn-sm"
              type="button"
              disabled={profileBusy}
              onClick={() => void saveEmail("")}
            >
              停用邮件
            </button>
          ) : null}
        </form>
        <div
          className="proposal-meta"
          style={{ marginTop: 8 }}
          aria-label="邮件偏好"
        >
          <label>
            <input
              type="checkbox"
              checked={notifyShipment}
              onChange={(event) => setNotifyShipment(event.target.checked)}
              data-testid="notify-email-shipment"
            />{" "}
            发货/物流
          </label>
          <label>
            <input
              type="checkbox"
              checked={notifyAfterSales}
              onChange={(event) => setNotifyAfterSales(event.target.checked)}
              data-testid="notify-email-after-sales"
            />{" "}
            售后/提案/工单
          </label>
          <label>
            <input
              type="checkbox"
              checked={notifyReturn}
              onChange={(event) => setNotifyReturn(event.target.checked)}
              data-testid="notify-email-return"
            />{" "}
            退货/换货
          </label>
          <span className="muted">修改后点上方「保存」生效</span>
        </div>
      </section>

      {loading ? (
        <div className="loading">加载售后记录…</div>
      ) : items.length === 0 &&
        tickets.length === 0 &&
        returns.length === 0 &&
        repairs.length === 0 &&
        deliveredOrders.length === 0 ? (
        <div className="empty-state">
          还没有售后记录。可在订单详情或售后助手中发起退款、补偿、改地址。
        </div>
      ) : (
        <>
          <section className="after-sales-section">
            <h3>售后申请</h3>
            {items.length === 0 ? (
              <div className="empty-state">没有售后申请。</div>
            ) : (
              <div className="admin-grid">
                {items.map((item) => (
                  <article
                    className="proposal-card"
                    key={item.id}
                    data-testid={`after-sales-${item.id}`}
                  >
                    <div className="proposal-head">
                      <div className="proposal-title">
                        <strong>
                          #{item.id} {actionTypeLabel(item.action_type)}
                        </strong>
                        <StatusBadge
                          label={proposalStatusLabel(item.status)}
                          status={item.status}
                        />
                      </div>
                      <span className="muted">
                        {formatDateTime(item.created_at)}
                      </span>
                    </div>
                    <div className="proposal-meta">
                      <span>订单 {item.order_no}</span>
                      {item.amount_display ? (
                        <span>金额 {item.amount_display} 元</span>
                      ) : null}
                      <span>{statusHint(item)}</span>
                    </div>
                    <p className="after-sales-reason">
                      申请原因：{item.reason || "—"}
                    </p>
                    {item.status === "rejected" && item.message ? (
                      <div className="notice notice-warn">{item.message}</div>
                    ) : null}
                    {item.status === "executed" && item.commerce_ref ? (
                      <p className="muted">
                        处理引用 {item.commerce_ref}
                        {item.executed_at
                          ? ` · ${formatDateTime(item.executed_at)}`
                          : ""}
                      </p>
                    ) : null}
                    {item.attachment_ids.length > 0 ? (
                      <div className="attachment-grid">
                        {item.attachment_ids.map((attachmentId) => (
                          <a
                            key={attachmentId}
                            href={`/api/store/attachments/${attachmentId}`}
                            target="_blank"
                            rel="noreferrer"
                          >
                            <img
                              src={`/api/store/attachments/${attachmentId}`}
                              alt="申请凭证"
                            />
                          </a>
                        ))}
                      </div>
                    ) : null}
                    {item.status === "pending" ? (
                      <div className="proposal-actions">
                        <input
                          type="file"
                          multiple
                          accept="image/png,image/jpeg,image/webp"
                          data-testid={`proposal-files-${item.id}`}
                          onChange={(event) =>
                            setProposalFiles((previous) => ({
                              ...previous,
                              [item.id]: Array.from(event.target.files ?? []).slice(
                                0,
                                3,
                              ),
                            }))
                          }
                          aria-label={`售后申请 ${item.id} 凭证`}
                        />
                        <button
                          className="btn btn-ghost btn-sm"
                          type="button"
                          data-testid={`attach-evidence-${item.id}`}
                          disabled={
                            busyId === item.id ||
                            (proposalFiles[item.id] ?? []).length === 0
                          }
                          onClick={() => void attachEvidence(item)}
                        >
                          补充凭证
                        </button>
                        <button
                          className="btn btn-ghost btn-sm"
                          type="button"
                          data-testid={`withdraw-${item.id}`}
                          disabled={busyId === item.id}
                          onClick={() => void withdraw(item)}
                        >
                          {busyId === item.id ? "撤回中" : "撤回申请"}
                        </button>
                      </div>
                    ) : null}
                  </article>
                ))}
              </div>
            )}
          </section>

          <section className="after-sales-section">
            <div className="section-head" style={{ marginBottom: 10 }}>
              <h3 style={{ margin: 0 }}>退货</h3>
              <Link className="btn btn-ghost btn-sm" href="/help">
                查看售后政策
              </Link>
            </div>
            {deliveredOrders.length > 0 ? (
              <form className="return-form" onSubmit={createReturn}>
                <div className="field">
                  <label htmlFor="return-order">订单</label>
                  <select
                    id="return-order"
                    data-testid="return-order"
                    value={returnOrderNo}
                    onChange={(event) => setReturnOrderNo(event.target.value)}
                  >
                    {deliveredOrders.map((order) => (
                      <option key={order.order_no} value={order.order_no}>
                        {order.order_no} · {order.item_count} 件 ·{" "}
                        {order.payable_display} 元
                      </option>
                    ))}
                  </select>
                  {eligibilityBusy ? (
                    <span className="muted">正在检查退货资格…</span>
                  ) : null}
                  {!eligibilityBusy && returnEligibility ? (
                    <div
                      className={
                        returnEligibility.eligible
                          ? "notice notice-success"
                          : "notice notice-error"
                      }
                      data-testid="return-eligibility"
                    >
                      {returnEligibility.eligible
                        ? `${
                            returnEligibility.items.some(
                              (item) => !item.eligible,
                            )
                              ? "部分商品可退"
                              : "可申请退货"
                          }：签收于 ${
                            returnEligibility.delivered_at
                              ? formatDateTime(returnEligibility.delivered_at)
                              : "—"
                          }，${
                            returnEligibility.deadline
                              ? `默认截止 ${formatDateTime(returnEligibility.deadline)}`
                              : "未记录签收截止日"
                          }（签收后 ${returnEligibility.window_days} 天为未配置类目的默认窗口）`
                        : returnEligibility.reason}
                    </div>
                  ) : null}
                  {returnEligibility?.eligible &&
                  returnEligibility.return_shipping_fee_cents > 0 ? (
                    <span className="muted">
                      退款将扣除退货运费{" "}
                      {formatCents(
                        returnEligibility.return_shipping_fee_cents,
                      )}{" "}
                      元。
                    </span>
                  ) : null}
                </div>
                {returnDetailBusy ? (
                  <div className="field full">
                    <span className="muted">加载订单商品…</span>
                  </div>
                ) : returnDetail ? (
                  <div className="field full">
                    <label>退货商品</label>
                    <div
                      className="proposal-meta"
                      style={{
                        flexDirection: "column",
                        alignItems: "flex-start",
                        gap: 6,
                      }}
                    >
                      {returnDetail.items.map((item) => {
                        const itemEligibility = returnEligibility?.items.find(
                          (row) => row.sku === item.sku,
                        );
                        const eligible = itemEligibility
                          ? itemEligibility.eligible
                          : true;
                        return (
                          <label
                            key={item.sku}
                            className={eligible ? undefined : "muted"}
                          >
                            <input
                              type="checkbox"
                              data-testid={`return-item-${item.sku}`}
                              checked={selectedReturnSkus.includes(item.sku)}
                              disabled={!eligible}
                              onChange={(event) =>
                                setSelectedReturnSkus((previous) =>
                                  event.target.checked
                                    ? Array.from(
                                        new Set([...previous, item.sku]),
                                      )
                                    : previous.filter(
                                        (sku) => sku !== item.sku,
                                      ),
                                )
                              }
                            />{" "}
                            {item.title} · {item.sku} × {item.qty} ·{" "}
                            {item.line_total_display} 元
                            {itemEligibility ? (
                              itemEligibility.eligible ? (
                                <span className="muted">
                                  {" "}
                                  · 类目窗口 {itemEligibility.window_days} 天
                                  {itemEligibility.deadline
                                    ? ` · 截止 ${formatDateTime(itemEligibility.deadline)}`
                                    : ""}
                                </span>
                              ) : (
                                <span className="badge badge-muted">
                                  {" "}
                                  {itemEligibility.reason}
                                </span>
                              )
                            ) : null}
                          </label>
                        );
                      })}
                    </div>
                    {returnDetail.items.length > 1 ? (
                      <span className="muted">
                        可只勾选需要退回的商品；部分退货按所选商品金额退款，运费仅在整单退回时退。
                      </span>
                    ) : null}
                  </div>
                ) : null}
                <div className="field">
                  <label htmlFor="return-files">凭证图片（最多 3 张）</label>
                  <input
                    id="return-files"
                    data-testid="return-files"
                    type="file"
                    multiple
                    accept="image/png,image/jpeg,image/webp"
                    onChange={(event) => {
                      const files = Array.from(event.target.files ?? []);
                      if (files.length > 3) {
                        setError("最多选择 3 张图片");
                        setReturnFiles(files.slice(0, 3));
                      } else {
                        setReturnFiles(files);
                      }
                    }}
                  />
                  {returnFiles.length > 0 ? (
                    <span className="muted">
                      已选择 {returnFiles.map((file) => file.name).join("、")}
                    </span>
                  ) : null}
                </div>
                <div className="field">
                  <label htmlFor="return-resolution">处理方式</label>
                  <select
                    id="return-resolution"
                    data-testid="return-resolution"
                    value={returnResolution}
                    onChange={(event) =>
                      setReturnResolution(
                        event.target.value as "refund" | "exchange",
                      )
                    }
                  >
                    <option value="refund">退货退款</option>
                    <option
                      value="exchange"
                      disabled={
                        returnEligibility
                          ? !returnEligibility.allow_exchange
                          : false
                      }
                    >
                      换货
                    </option>
                  </select>
                </div>
                {returnResolution === "exchange" ? (
                  <div className="field full" data-testid="exchange-sku">
                    <label>
                      换货商品（留空为同款；仅支持同价或更低价）
                    </label>
                    <div className="exchange-sku-list">
                      {selectedReturnItems.map((item) => (
                        <label
                          className="exchange-sku-row"
                          key={item.sku}
                        >
                          <span>
                            {item.title} × {item.qty}
                          </span>
                          <select
                            data-testid={`exchange-sku-${item.sku}`}
                            value={exchangeSkus[item.sku] ?? ""}
                            onChange={(event) =>
                              setExchangeSkus((previous) => ({
                                ...previous,
                                [item.sku]: event.target.value,
                              }))
                            }
                          >
                            <option value="">同款换货</option>
                            {exchangeProducts
                              .filter(
                                (product) =>
                                  product.sku === item.sku ||
                                  product.price_cents <=
                                    item.unit_price_cents,
                              )
                              .map((product) => (
                                <option
                                  key={product.sku}
                                  value={product.sku}
                                >
                                  {product.title} ·{" "}
                                  {product.price_display} 元 · {product.sku}
                                </option>
                              ))}
                          </select>
                        </label>
                      ))}
                    </div>
                    <span className="muted">
                      可逐件指定换货商品；换更低价商品时，差价会生成退款提案，仍需店主审批。
                    </span>
                  </div>
                ) : null}
                <div className="field">
                  <label htmlFor="return-reason-category">原因分类</label>
                  <select
                    id="return-reason-category"
                    data-testid="return-reason-category"
                    value={returnReasonCategory}
                    onChange={(event) =>
                      setReturnReasonCategory(event.target.value)
                    }
                  >
                    <option value="quality">质量问题</option>
                    <option value="damaged">运输破损</option>
                    <option value="wrong_item">发错/少发</option>
                    <option value="size">尺寸不合适</option>
                    <option value="not_needed">不想要</option>
                    <option value="other">其他</option>
                  </select>
                </div>
                <div className="field">
                  <label htmlFor="return-reason">退货原因</label>
                  <input
                    id="return-reason"
                    data-testid="return-reason"
                    value={returnReason}
                    onChange={(event) => setReturnReason(event.target.value)}
                    placeholder="如：耳机有杂音，需要退货"
                    maxLength={1000}
                  />
                </div>
                <button
                  className="btn btn-primary"
                  type="submit"
                  data-testid="create-return"
                  disabled={
                    returnBusy ||
                    eligibilityBusy ||
                    (returnEligibility !== null && !returnEligibility.eligible) ||
                    (returnDetail !== null && selectedReturnSkus.length === 0)
                  }
                >
                  提交退货申请
                </button>
              </form>
            ) : (
              <div className="muted">
                暂无已签收订单；订单签收后才能申请退货。
              </div>
            )}

            {returns.length > 0 ? (
              <div className="admin-grid">
                {returns.map((row) => {
                  const form = trackingForm(row.id);
                  const pickupForm = pickupForms[row.id] ?? defaultPickupForm();
                  return (
                    <article
                      className="proposal-card"
                      key={row.id}
                      data-testid={`return-${row.id}`}
                    >
                      <div className="proposal-head">
                        <div className="proposal-title">
                          <strong>
                            退货 #{row.id} ·{" "}
                            {row.resolution === "exchange" ? "换货" : "退货退款"}
                          </strong>
                          <StatusBadge
                            label={returnStatusLabel(row.status)}
                            tone={returnTone(row.status)}
                          />
                        </div>
                        <span className="muted">
                          {formatDateTime(row.created_at)}
                        </span>
                      </div>
                      <div className="proposal-meta">
                        <span>订单 {row.order_no}</span>
                        {row.tracking_no ? (
                          <span>
                            回寄 {row.carrier} · {row.tracking_no}
                          </span>
                        ) : null}
                        {row.exchange_sku ? (
                          <span>换货商品 {row.exchange_sku}</span>
                        ) : null}
                        {row.refund_proposal_id ? (
                          <span>退款提案 #{row.refund_proposal_id} 待审批</span>
                        ) : null}
                        {row.refund_amount_cents !== null ? (
                          <span>
                            退款金额{" "}
                            {formatCents(row.refund_amount_cents)} 元
                          </span>
                        ) : null}
                        {row.inspection_deduction_cents > 0 ? (
                          <>
                            <span>
                              质检扣减{" "}
                              {formatCents(row.inspection_deduction_cents)}{" "}
                              元
                            </span>
                            {row.items
                              .filter(
                                (line) =>
                                  line.inspection_deduction_cents > 0,
                              )
                              .map((line) => (
                                <span
                                  key={`customer-item-deduction-${row.id}-${line.id}`}
                                >
                                  {line.sku} 扣减{" "}
                                  {formatCents(
                                    line.inspection_deduction_cents,
                                  )}{" "}
                                  元
                                </span>
                              ))}
                          </>
                        ) : null}
                        {row.inspection_note ? (
                          <span>质检说明 {row.inspection_note}</span>
                        ) : null}
                        {row.replacement_order_no ? (
                          <span>换货订单 {row.replacement_order_no}</span>
                        ) : null}
                      </div>
                      {row.resolution === "exchange" &&
                      row.exchange_supplement_cents > 0 ? (
                        <div
                          className="exchange-supplement"
                          data-testid={`exchange-supplement-${row.id}`}
                        >
                          <p className="proposal-meta">
                            <span>换货补差价</span>
                            <StatusBadge
                              label={
                                row.exchange_payment_status === "paid"
                                  ? "已支付"
                                  : row.exchange_payment_status === "cancelled"
                                    ? "已取消"
                                    : "待支付"
                              }
                              tone={
                                row.exchange_payment_status === "paid"
                                  ? "badge-ok"
                                  : row.exchange_payment_status ===
                                      "cancelled"
                                    ? "badge-muted"
                                    : "badge-warn"
                              }
                            />
                            <span>
                              {formatCents(row.exchange_supplement_cents)} 元
                            </span>
                            {row.exchange_payment_ref ? (
                              <span>支付单 {row.exchange_payment_ref}</span>
                            ) : null}
                          </p>
                          {row.exchange_payment_status === "pending" &&
                          row.exchange_payment_deadline ? (
                            <p
                              className="muted"
                              data-testid={`exchange-deadline-${row.id}`}
                            >
                              请在{" "}
                              {formatDateTime(row.exchange_payment_deadline)}{" "}
                              前支付，逾期将自动取消换货并生成退款提案。
                            </p>
                          ) : null}
                          {row.exchange_payment_status === "pending" ? (
                            <div className="proposal-actions">
                              <button
                                className="btn btn-primary btn-sm"
                                type="button"
                                data-testid={`pay-exchange-supplement-${row.id}`}
                                disabled={supplementBusyId === row.id}
                                onClick={() => void payExchangeSupplement(row)}
                              >
                                支付换货补差价
                              </button>
                              <button
                                className="btn btn-ghost btn-sm"
                                type="button"
                                data-testid={`cancel-exchange-${row.id}`}
                                disabled={
                                  exchangeCancelBusyId === row.id
                                }
                                onClick={() => void cancelExchange(row)}
                              >
                                放弃换货并申请退款
                              </button>
                            </div>
                          ) : row.exchange_payment_status === "cancelled" ? (
                            <p
                              className="muted"
                              data-testid={`exchange-cancelled-${row.id}`}
                            >
                              换货已取消 · {row.exchange_cancel_reason}
                              {row.refund_proposal_id
                                ? ` · 退款提案 #${row.refund_proposal_id} 待商家审批`
                                : " · 无需退款"}
                            </p>
                          ) : (
                            <p className="muted">
                              补差价已支付，等待换货商品发货。
                            </p>
                          )}
                        </div>
                      ) : null}
                      {row.appeal ? (
                        <div
                          className="appeal-panel"
                          data-testid={`return-appeal-${row.id}`}
                        >
                          <p className="proposal-meta">
                            <span>扣减申诉</span>
                            <StatusBadge
                              label={appealStatusLabel(row.appeal.status)}
                              tone={appealTone(row.appeal.status)}
                            />
                            <span>
                              申请 {formatCents(row.appeal.requested_cents)} 元
                            </span>
                            {row.appeal.approved_cents !== null ? (
                              <span>
                                核准{" "}
                                {formatCents(row.appeal.approved_cents)} 元
                              </span>
                            ) : null}
                          </p>
                          <p className="after-sales-reason">
                            申诉原因：{row.appeal.reason}
                          </p>
                          {row.appeal.attachment_ids.length > 0 ? (
                            <div className="attachment-grid">
                              {row.appeal.attachment_ids.map(
                                (attachmentId) => (
                                  <a
                                    key={`appeal-attachment-${row.appeal!.id}-${attachmentId}`}
                                    href={`/api/store/attachments/${attachmentId}`}
                                    target="_blank"
                                    rel="noreferrer"
                                  >
                                    <img
                                      src={`/api/store/attachments/${attachmentId}`}
                                      alt="申诉凭证"
                                    />
                                  </a>
                                ),
                              )}
                            </div>
                          ) : null}
                          {row.appeal.resolution_note ? (
                            <p className="muted">
                              处理说明：{row.appeal.resolution_note}
                            </p>
                          ) : null}
                          {row.appeal.status === "pending" ? (
                            <button
                              className="btn btn-ghost btn-sm"
                              type="button"
                              data-testid={`withdraw-appeal-${row.id}`}
                              disabled={appealBusyId === row.id}
                              onClick={() => void withdrawReturnAppeal(row)}
                            >
                              撤回申诉
                            </button>
                          ) : null}
                        </div>
                      ) : row.can_appeal ? (
                        <div
                          className="appeal-panel"
                          data-testid={`appeal-form-${row.id}`}
                        >
                          <p className="muted">
                            对质检扣减有异议？可在{" "}
                            {row.appeal_deadline
                              ? formatDateTime(row.appeal_deadline)
                              : ""}{" "}
                            前提交一次申诉。
                          </p>
                          <textarea
                            value={appealReasons[row.id] ?? ""}
                            maxLength={1000}
                            placeholder="说明商品实际状况与申诉理由"
                            data-testid={`appeal-reason-${row.id}`}
                            onChange={(event) =>
                              setAppealReasons((previous) => ({
                                ...previous,
                                [row.id]: event.target.value,
                              }))
                            }
                          />
                          <label className="muted">
                            补充凭证（最多 3 张）
                            <input
                              type="file"
                              accept="image/*"
                              multiple
                              data-testid={`appeal-files-${row.id}`}
                              onChange={(event) =>
                                setAppealFiles((previous) => ({
                                  ...previous,
                                  [row.id]: Array.from(
                                    event.target.files ?? [],
                                  ).slice(0, 3),
                                }))
                              }
                            />
                          </label>
                          {appealFiles[row.id]?.length ? (
                            <p className="muted">
                              已选择 {appealFiles[row.id].length} 张图片
                            </p>
                          ) : null}
                          <button
                            className="btn btn-primary btn-sm"
                            type="button"
                            data-testid={`submit-appeal-${row.id}`}
                            disabled={
                              appealBusyId === row.id ||
                              !(appealReasons[row.id] ?? "").trim()
                            }
                            onClick={() => void submitReturnAppeal(row)}
                          >
                            提交扣减申诉
                          </button>
                        </div>
                      ) : row.appeal_deadline ? (
                        <p
                          className="muted"
                          data-testid={`appeal-expired-${row.id}`}
                        >
                          扣减申诉期已于{" "}
                          {formatDateTime(row.appeal_deadline)} 结束。
                        </p>
                      ) : null}
                      {row.tracking_events.length > 0 ? (
                        <ol
                          className="shipment-timeline return-tracking-timeline"
                          data-testid={`customer-return-tracking-${row.id}`}
                        >
                          {row.tracking_events.map((event) => (
                            <li key={`${event.status}-${event.happened_at}`}>
                              <span className="timeline-node">
                                {event.label}
                              </span>
                              <span className="timeline-desc">
                                {event.description}
                              </span>
                              <time>
                                {formatDateTime(event.happened_at)}
                              </time>
                            </li>
                          ))}
                        </ol>
                      ) : null}
                      {row.replacement_shipments.length > 0 ? (
                        <div className="replacement-shipments">
                          <p className="muted">换货补发</p>
                          {row.replacement_shipments.map((shipment) => (
                            <div
                              key={shipment.id}
                              data-testid={`replacement-shipment-${row.id}-${shipment.id}`}
                            >
                              <p className="muted">
                                包裹 #{shipment.package_no} ·{" "}
                                {shipment.carrier} {shipment.tracking_no}
                                {shipment.current_node
                                  ? ` · ${shipment.current_node}`
                                  : ""}
                              </p>
                              {shipment.events.length > 0 ? (
                                <ol
                                  className="shipment-timeline return-tracking-timeline"
                                  data-testid={`replacement-tracking-${row.id}-${shipment.id}`}
                                >
                                  {shipment.events.map((event) => (
                                    <li
                                      key={`${event.node}-${event.happened_at}`}
                                    >
                                      <span className="timeline-node">
                                        {event.node}
                                      </span>
                                      <span className="timeline-desc">
                                        {event.description}
                                      </span>
                                      <time>
                                        {formatDateTime(event.happened_at)}
                                      </time>
                                    </li>
                                  ))}
                                </ol>
                              ) : null}
                            </div>
                          ))}
                        </div>
                      ) : null}
                      {row.items.length > 0 ? (
                        <p className="muted">
                          退回：
                          {row.items
                            .map(
                              (line) =>
                                `${line.title} × ${line.qty}` +
                                (line.exchange_sku
                                  ? ` → 换 ${line.exchange_sku}`
                                  : "") +
                                (line.disposition === "restock"
                                  ? "（已回补）"
                                  : line.disposition === "damaged"
                                    ? "（已报损）"
                                    : "") +
                                (line.responsibility
                                  ? `（${returnResponsibilityLabel(line.responsibility)}）`
                                  : ""),
                            )
                            .join("、")}
                        </p>
                      ) : null}
                      <p className="after-sales-reason">
                        退货原因：{returnReasonLabel(row.reason_category)} ·{" "}
                        {row.reason || "—"}
                      </p>
                      {row.attachment_ids.length > 0 ? (
                        <div className="attachment-grid">
                          {row.attachment_ids.map((attachmentId) => (
                            <a
                              key={attachmentId}
                              href={`/api/store/attachments/${attachmentId}`}
                              target="_blank"
                              rel="noreferrer"
                            >
                              <img
                                src={`/api/store/attachments/${attachmentId}`}
                                alt="退货凭证"
                              />
                            </a>
                          ))}
                        </div>
                      ) : null}
                      {row.inspection_attachment_ids.length > 0 ? (
                        <div
                          className="attachment-grid"
                          data-testid={`customer-inspection-images-${row.id}`}
                        >
                          {row.inspection_attachment_ids.map(
                            (attachmentId) => (
                              <a
                                key={attachmentId}
                                href={`/api/store/attachments/${attachmentId}`}
                                target="_blank"
                                rel="noreferrer"
                              >
                                <img
                                  src={`/api/store/attachments/${attachmentId}`}
                                  alt="质检照片"
                                />
                              </a>
                            ),
                          )}
                        </div>
                      ) : null}
                      {row.status === "requested" ? (
                        <div className="proposal-actions" style={{ alignItems: "flex-start", flexWrap: "wrap" }}>
                          {row.attachment_ids.length < 3 ? (
                            <>
                              <input
                                type="file"
                                accept="image/png,image/jpeg,image/webp"
                                multiple
                                data-testid={`return-evidence-${row.id}`}
                                onChange={(event) => {
                                  const files = Array.from(
                                    event.target.files ?? [],
                                  );
                                  setReturnEvidenceFiles((previous) => ({
                                    ...previous,
                                    [row.id]: files,
                                  }));
                                }}
                              />
                              <button
                                className="btn btn-ghost btn-sm"
                                type="button"
                                data-testid={`submit-return-evidence-${row.id}`}
                                disabled={
                                  returnBusy ||
                                  (returnEvidenceFiles[row.id]?.length ?? 0) === 0
                                }
                                onClick={() => void attachReturnEvidence(row)}
                              >
                                补充凭证
                              </button>
                            </>
                          ) : (
                            <span className="muted">凭证已满 3 张</span>
                          )}
                          <button
                            className="btn btn-ghost btn-sm"
                            type="button"
                            data-testid={`withdraw-return-${row.id}`}
                            disabled={returnBusy}
                            onClick={() => void withdrawReturn(row.id)}
                          >
                            撤回申请
                          </button>
                        </div>
                      ) : null}
                      {row.status === "approved" ? (
                        <>
                          <p className="muted">
                            请寄回：{row.return_address || "等待店主提供地址"}
                          </p>
                          {row.pickup ? (
                            <div
                              className="pickup-panel"
                              data-testid={`return-pickup-${row.id}`}
                            >
                              <p className="proposal-meta">
                                <span>上门取件</span>
                                <StatusBadge
                                  label={pickupStatusLabel(row.pickup.status)}
                                  tone={pickupTone(row.pickup.status)}
                                />
                                <span>
                                  {row.pickup.scheduled_date}{" "}
                                  {row.pickup.time_window}
                                </span>
                                {row.pickup.status === "scheduled" ? (
                                  <span>取件单 {row.pickup.pickup_no}</span>
                                ) : null}
                              </p>
                              <p className="muted">
                                取件地址：{row.pickup.address_region}{" "}
                                {row.pickup.address_city}{" "}
                                {row.pickup.address_detail} ·{" "}
                                {row.pickup.address_recipient}{" "}
                                {row.pickup.address_phone}
                              </p>
                              {row.pickup.cancel_reason ? (
                                <p className="muted">
                                  取消原因：{row.pickup.cancel_reason}
                                </p>
                              ) : null}
                              {row.pickup.status === "scheduled" ? (
                                <button
                                  className="btn btn-ghost btn-sm"
                                  type="button"
                                  data-testid={`cancel-pickup-${row.id}`}
                                  disabled={pickupBusyId === row.id}
                                  onClick={() => void cancelReturnPickup(row)}
                                >
                                  取消取件预约
                                </button>
                              ) : null}
                            </div>
                          ) : null}
                          {row.can_schedule_pickup ? (
                            <div
                              className="pickup-form"
                              data-testid={`pickup-form-${row.id}`}
                            >
                              <p className="muted">
                                可预约上门取件，也可自行寄回。
                              </p>
                              <input
                                type="date"
                                min={pickupDate(0)}
                                max={pickupDate(7)}
                                value={pickupForm.scheduled_date}
                                data-testid={`pickup-date-${row.id}`}
                                onChange={(event) =>
                                  setPickupForms((previous) => ({
                                    ...previous,
                                    [row.id]: {
                                      ...pickupForm,
                                      scheduled_date: event.target.value,
                                    },
                                  }))
                                }
                              />
                              <select
                                value={pickupForm.time_window}
                                data-testid={`pickup-window-${row.id}`}
                                onChange={(event) =>
                                  setPickupForms((previous) => ({
                                    ...previous,
                                    [row.id]: {
                                      ...pickupForm,
                                      time_window: event.target.value,
                                    },
                                  }))
                                }
                              >
                                <option value="09:00-12:00">09:00-12:00</option>
                                <option value="13:00-18:00">13:00-18:00</option>
                                <option value="18:00-21:00">18:00-21:00</option>
                              </select>
                              <button
                                className="btn btn-primary btn-sm"
                                type="button"
                                data-testid={`schedule-pickup-${row.id}`}
                                disabled={pickupBusyId === row.id}
                                onClick={() => void scheduleReturnPickup(row)}
                              >
                                预约上门取件
                              </button>
                            </div>
                          ) : null}
                          {row.pickup?.status !== "scheduled" ? (
                            <div className="return-tracking-form">
                              <input
                                placeholder="承运商"
                                value={form.carrier}
                                maxLength={64}
                                data-testid={`return-carrier-${row.id}`}
                                onChange={(event) =>
                                  setTrackingForms((previous) => ({
                                    ...previous,
                                    [row.id]: {
                                      ...form,
                                      carrier: event.target.value,
                                    },
                                  }))
                                }
                              />
                              <input
                                placeholder="退货运单号"
                                value={form.tracking_no}
                                maxLength={64}
                                data-testid={`return-tracking-${row.id}`}
                                onChange={(event) =>
                                  setTrackingForms((previous) => ({
                                    ...previous,
                                    [row.id]: {
                                      ...form,
                                      tracking_no: event.target.value,
                                    },
                                  }))
                                }
                              />
                              <button
                                className="btn btn-primary btn-sm"
                                type="button"
                                data-testid={`submit-return-tracking-${row.id}`}
                                disabled={returnBusy}
                                onClick={() => void submitTracking(row.id)}
                              >
                                登记回寄
                              </button>
                            </div>
                          ) : null}
                        </>
                      ) : null}
                      {row.status === "rejected" && row.rejected_reason ? (
                        <div className="notice notice-warn">
                          驳回原因：{row.rejected_reason}
                        </div>
                      ) : null}
                      {row.status === "received" ? (
                        <p className="muted">
                          已收货
                          {row.received_at
                            ? ` · ${formatDateTime(row.received_at)}`
                            : ""}
                          {row.replacement_order_no
                            ? row.replacement_shipments.length > 0
                              ? `；换货订单 ${row.replacement_order_no} 已补发。`
                              : `；换货订单 ${row.replacement_order_no} 等待发货。`
                            : "；退款提案等待店主审批。"}
                        </p>
                      ) : null}
                    </article>
                  );
                })}
              </div>
            ) : null}
          </section>

          <section className="after-sales-section">
            <h3>保修维修</h3>
            {deliveredOrders.length > 0 ? (
              <form className="return-form" onSubmit={createRepair}>
                <div className="field">
                  <label htmlFor="repair-order">订单</label>
                  <select
                    id="repair-order"
                    data-testid="repair-order"
                    value={repairOrderNo}
                    onChange={(event) =>
                      setRepairOrderNo(event.target.value)
                    }
                  >
                    {deliveredOrders.map((order) => (
                      <option key={order.order_no} value={order.order_no}>
                        {order.order_no} · {order.item_count} 件
                      </option>
                    ))}
                  </select>
                </div>
                <div className="field">
                  <label htmlFor="repair-sku">商品</label>
                  <select
                    id="repair-sku"
                    data-testid="repair-sku"
                    value={repairSku}
                    onChange={(event) => setRepairSku(event.target.value)}
                    disabled={repairDetailBusy}
                  >
                    {repairDetailBusy ? (
                      <option value="">加载中…</option>
                    ) : null}
                    {(repairDetail?.items ?? []).map((item) => (
                      <option key={item.sku} value={item.sku}>
                        {item.title} · {item.sku}
                      </option>
                    ))}
                  </select>
                </div>
                {repairEligibilityBusy ? (
                  <p className="muted">检查保修资格…</p>
                ) : selectedRepairEligibility ? (
                  <div
                    className={
                      selectedRepairEligibility.eligible
                        ? "notice notice-success"
                        : "notice notice-warn"
                    }
                    data-testid="repair-eligibility"
                  >
                    {selectedRepairEligibility.eligible &&
                    selectedRepairEligibility.deadline
                      ? `保修至 ${formatDateTime(selectedRepairEligibility.deadline)}（签收后 ${selectedRepairEligibility.warranty_months} 个月）`
                      : selectedRepairEligibility.reason}
                  </div>
                ) : null}
                <div className="field full">
                  <label htmlFor="repair-issue">故障描述</label>
                  <textarea
                    id="repair-issue"
                    data-testid="repair-issue"
                    value={repairIssue}
                    onChange={(event) =>
                      setRepairIssue(event.target.value)
                    }
                    maxLength={2000}
                    rows={3}
                    placeholder="如：左耳无声，已尝试重新配对"
                  />
                </div>
                <div className="field">
                  <label htmlFor="repair-files">
                    凭证图片（最多 3 张）
                  </label>
                  <input
                    id="repair-files"
                    data-testid="repair-files"
                    type="file"
                    multiple
                    accept="image/png,image/jpeg,image/webp"
                    onChange={(event) => {
                      const files = Array.from(event.target.files ?? []);
                      setRepairFiles(files.slice(0, 3));
                    }}
                  />
                  {repairFiles.length > 0 ? (
                    <span className="muted">
                      已选择{" "}
                      {repairFiles.map((file) => file.name).join("、")}
                    </span>
                  ) : null}
                </div>
                <button
                  className="btn btn-primary"
                  type="submit"
                  data-testid="create-repair"
                  disabled={
                    repairBusy ||
                    !repairSku ||
                    !repairIssue.trim() ||
                    (selectedRepairEligibility
                      ? !selectedRepairEligibility.eligible
                      : false)
                  }
                >
                  {repairBusy ? "提交中" : "提交维修申请"}
                </button>
                <p className="muted">
                  保修期按下单时的商品配置快照计算；维修不产生退款。
                </p>
              </form>
            ) : (
              <div className="muted">
                暂无已签收订单；订单签收后才能申请维修。
              </div>
            )}

            {repairs.length > 0 ? (
              <div className="admin-grid">
                {repairs.map((repair) => {
                  const form = repairTracking[repair.id] ?? {
                    carrier: "",
                    tracking_no: "",
                  };
                  return (
                    <article
                      className="proposal-card"
                      key={repair.id}
                      data-testid={`repair-${repair.id}`}
                    >
                      <div className="proposal-head">
                        <div className="proposal-title">
                          <strong>
                            维修 #{repair.id} · {repair.sku}
                          </strong>
                          <StatusBadge
                            label={repairStatusLabel(repair.status)}
                            tone={repairTone(repair.status)}
                          />
                        </div>
                        <span className="muted">
                          {formatDateTime(repair.created_at)}
                        </span>
                      </div>
                      <div className="proposal-meta">
                        <span>订单 {repair.order_no}</span>
                        <span>{repair.title}</span>
                        {repair.tracking_no ? (
                          <span>
                            回寄 {repair.carrier} · {repair.tracking_no}
                          </span>
                        ) : null}
                        {repair.return_tracking_no ? (
                          <span>
                            维修寄回 {repair.return_carrier} ·{" "}
                            {repair.return_tracking_no}
                          </span>
                        ) : null}
                      </div>
                      <p className="after-sales-reason">
                        故障：{repair.issue}
                      </p>
                      {repair.attachment_ids.length > 0 ? (
                        <div className="attachment-grid">
                          {repair.attachment_ids.map((attachmentId) => (
                            <a
                              key={attachmentId}
                              href={`/api/store/attachments/${attachmentId}`}
                              target="_blank"
                              rel="noreferrer"
                            >
                              <img
                                src={`/api/store/attachments/${attachmentId}`}
                                alt="维修凭证"
                              />
                            </a>
                          ))}
                        </div>
                      ) : null}
                      {repair.return_address ? (
                        <p className="muted">
                          寄回地址：{repair.return_address}
                        </p>
                      ) : null}
                      {repair.rejected_reason ? (
                        <div className="notice notice-warn">
                          驳回原因：{repair.rejected_reason}
                        </div>
                      ) : null}
                      {repair.resolution_note ? (
                        <p className="muted">
                          处理结果：{repair.resolution_note}
                        </p>
                      ) : null}

                      {repair.quote_status !== "none" ? (
                        <div
                          className="repair-quote"
                          data-testid={`repair-quote-${repair.id}`}
                        >
                          <p className="proposal-meta">
                            <span>维修报价</span>
                            <StatusBadge
                              label={repairQuoteStatusLabel(
                                repair.quote_status,
                              )}
                              tone={repairQuoteTone(repair.quote_status)}
                            />
                            {repair.quote_revised_at ? (
                              <StatusBadge
                                label="已更新"
                                tone="badge-neutral"
                              />
                            ) : null}
                            <span>
                              {formatCents(repair.quote_cents ?? 0)} 元
                            </span>
                            {repair.quote_payment_ref ? (
                              <span>支付单 {repair.quote_payment_ref}</span>
                            ) : null}
                          </p>
                          <p className="muted">报价说明：{repair.quote_note}</p>
                          {repair.quote_status === "pending" ? (
                            <div className="proposal-actions">
                              <button
                                className="btn btn-primary btn-sm"
                                type="button"
                                data-testid={`repair-quote-accept-${repair.id}`}
                                disabled={repairBusyId === repair.id}
                                onClick={() =>
                                  void actRepairQuote(repair, "accept")
                                }
                              >
                                接受报价
                              </button>
                              <button
                                className="btn btn-ghost btn-sm"
                                type="button"
                                data-testid={`repair-quote-decline-${repair.id}`}
                                disabled={repairBusyId === repair.id}
                                onClick={() =>
                                  void actRepairQuote(repair, "decline")
                                }
                              >
                                拒绝报价
                              </button>
                            </div>
                          ) : null}
                          {repair.quote_status === "accepted" ? (
                            <div className="proposal-actions">
                              <button
                                className="btn btn-primary btn-sm"
                                type="button"
                                data-testid={`repair-quote-pay-${repair.id}`}
                                disabled={repairBusyId === repair.id}
                                onClick={() =>
                                  void actRepairQuote(repair, "pay")
                                }
                              >
                                支付维修费
                              </button>
                            </div>
                          ) : null}
                          {repair.quote_status === "declined" ? (
                            <p className="muted">
                              已拒绝报价，商家将原样寄回维修件。
                            </p>
                          ) : null}
                          {repair.quote_status === "paid" ? (
                            <p className="muted">
                              维修费已支付，商家处理中。
                            </p>
                          ) : null}
                        </div>
                      ) : null}

                      {repair.status === "requested" ? (
                        <div className="proposal-actions">
                          <button
                            className="btn btn-ghost btn-sm"
                            type="button"
                            data-testid={`withdraw-repair-${repair.id}`}
                            disabled={repairBusyId === repair.id}
                            onClick={() => void withdrawRepair(repair.id)}
                          >
                            撤回申请
                          </button>
                        </div>
                      ) : null}

                      {repair.status === "approved" ? (
                        <div className="proposal-actions">
                          <input
                            value={form.carrier}
                            onChange={(event) =>
                              setRepairTracking((previous) => ({
                                ...previous,
                                [repair.id]: {
                                  ...form,
                                  carrier: event.target.value,
                                },
                              }))
                            }
                            placeholder="承运商"
                            maxLength={64}
                          />
                          <input
                            value={form.tracking_no}
                            onChange={(event) =>
                              setRepairTracking((previous) => ({
                                ...previous,
                                [repair.id]: {
                                  ...form,
                                  tracking_no: event.target.value,
                                },
                              }))
                            }
                            placeholder="维修回寄单号"
                            maxLength={64}
                          />
                          <button
                            className="btn btn-primary btn-sm"
                            type="button"
                            data-testid={`repair-tracking-submit-${repair.id}`}
                            disabled={repairBusyId === repair.id}
                            onClick={() => void submitRepairTracking(repair)}
                          >
                            登记回寄
                          </button>
                        </div>
                      ) : null}

                      {repair.status === "return_shipped" ? (
                        <div className="proposal-actions">
                          <button
                            className="btn btn-primary btn-sm"
                            type="button"
                            data-testid={`repair-confirm-${repair.id}`}
                            disabled={repairBusyId === repair.id}
                            onClick={() => void confirmRepairReceived(repair)}
                          >
                            确认收到维修件
                          </button>
                        </div>
                      ) : null}
                    </article>
                  );
                })}
              </div>
            ) : null}
          </section>

          <section className="after-sales-section">
            <h3>人工工单</h3>
            {tickets.length === 0 ? (
              <div className="empty-state">没有人工工单。</div>
            ) : (
              <div className="admin-grid">
                {tickets.map((ticket) => (
                  <article
                    className="proposal-card"
                    key={ticket.id}
                    data-testid={`customer-handoff-${ticket.id}`}
                  >
                    <div className="proposal-head">
                      <div className="proposal-title">
                        <strong>{ticket.ticket_ref}</strong>
                        <StatusBadge
                          label={handoffStatusLabel(ticket.status)}
                          tone={handoffTone(ticket.status)}
                        />
                        <StatusBadge
                          label={`${handoffQueueLabel(ticket.queue)}队列`}
                          tone="badge-neutral"
                        />
                      </div>
                      <span className="muted">
                        {formatDateTime(ticket.updated_at)}
                      </span>
                    </div>
                    <div className="proposal-meta">
                      <span>意图 {handoffIntentLabel(ticket.intent)}</span>
                      {ticket.resolved_at ? (
                        <span>解决时间 {formatDateTime(ticket.resolved_at)}</span>
                      ) : null}
                    </div>
                    <div className="handoff-messages">
                      <p>
                        <strong>我</strong>
                        <span>{ticket.last_user_message || "—"}</span>
                      </p>
                      <p>
                        <strong>AI</strong>
                        <span>{ticket.last_reply || "—"}</span>
                      </p>
                    </div>
                    {ticket.status === "resolved" ? (
                      ticket.rating_score !== null &&
                      ticket.rating_score !== undefined ? (
                        <div
                          className="notice notice-success"
                          data-testid={`ticket-rating-${ticket.id}`}
                        >
                          已评价 {ticket.rating_score} 星
                          {ticket.rating_comment
                            ? `：${ticket.rating_comment}`
                            : ""}
                        </div>
                      ) : (
                        <div
                          className="proposal-actions"
                          style={{ marginTop: 10, flexWrap: "wrap" }}
                        >
                          <select
                            data-testid={`rating-score-${ticket.id}`}
                            value={ratingScores[ticket.id] ?? ""}
                            onChange={(event) =>
                              setRatingScores((previous) => ({
                                ...previous,
                                [ticket.id]: Number(event.target.value),
                              }))
                            }
                            aria-label={`评价工单 ${ticket.ticket_ref}`}
                          >
                            <option value="">选择评分</option>
                            {[1, 2, 3, 4, 5].map((score) => (
                              <option key={score} value={score}>
                                {score} 星
                              </option>
                            ))}
                          </select>
                          <input
                            data-testid={`rating-comment-${ticket.id}`}
                            value={ratingComments[ticket.id] ?? ""}
                            onChange={(event) =>
                              setRatingComments((previous) => ({
                                ...previous,
                                [ticket.id]: event.target.value,
                              }))
                            }
                            placeholder="评价内容（可选）"
                            maxLength={500}
                            style={{ flex: 1, minWidth: 160 }}
                          />
                          <button
                            className="btn btn-primary btn-sm"
                            type="button"
                            data-testid={`submit-rating-${ticket.id}`}
                            disabled={
                              !ratingScores[ticket.id] ||
                              ratingBusyId === ticket.id
                            }
                            onClick={() => void submitRating(ticket)}
                          >
                            {ratingBusyId === ticket.id
                              ? "提交中"
                              : "提交评价"}
                          </button>
                        </div>
                      )
                    ) : null}
                  </article>
                ))}
              </div>
            )}
          </section>
        </>
      )}
    </>
  );
}
