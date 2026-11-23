"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { StatusBadge } from "@/components/StatusBadge";
import { api, errorMessage } from "@/lib/api";
import { formatDateTime, invoiceStatusLabel } from "@/lib/status";
import type {
  AfterSalesChargeLedger,
  FinanceSummary,
  InvoiceView,
  RefundSettlementRecord,
} from "@/lib/types";

const KIND_LABELS: Record<string, string> = {
  payment: "支付",
  refund: "退款",
  compensation: "补偿",
  exchange_supplement: "换货补差",
  repair_fee: "维修费",
};

function settlementLabel(status: string): string {
  if (status === "processing") return "渠道处理中";
  if (status === "succeeded") return "渠道已到账";
  if (status === "failed") return "渠道失败";
  return status;
}

function settlementTone(status: string): string {
  if (status === "succeeded") return "badge-ok";
  if (status === "failed") return "badge-danger";
  return "badge-warn";
}

export function AdminFinance({ role }: { role: "owner" | "agent" }) {
  const [days, setDays] = useState("30");
  const [summary, setSummary] = useState<FinanceSummary | null>(null);
  const [charges, setCharges] = useState<AfterSalesChargeLedger | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [invoices, setInvoices] = useState<InvoiceView[]>([]);
  const [invoiceStatus, setInvoiceStatus] = useState("requested");
  const [invoiceBusyId, setInvoiceBusyId] = useState<number | null>(null);
  const [invoiceNotice, setInvoiceNotice] = useState("");
  const [invoiceNumbers, setInvoiceNumbers] = useState<
    Record<number, string>
  >({});
  const [invoiceReasons, setInvoiceReasons] = useState<
    Record<number, string>
  >({});
  const invoiceSeq = useRef(0);
  const [refunds, setRefunds] = useState<RefundSettlementRecord[]>([]);
  const [refundStatus, setRefundStatus] = useState("all");
  const [refundBusyId, setRefundBusyId] = useState<number | null>(null);
  const [refundNotice, setRefundNotice] = useState("");
  const [refundReferences, setRefundReferences] = useState<
    Record<number, string>
  >({});
  const [refundFailureReasons, setRefundFailureReasons] = useState<
    Record<number, string>
  >({});
  const refundSeq = useRef(0);

  const loadRefunds = useCallback(async () => {
    const seq = ++refundSeq.current;
    try {
      const rows = await api<RefundSettlementRecord[]>(
        `/api/admin/finance/refunds?status=${refundStatus}&limit=50`,
      );
      if (seq === refundSeq.current) setRefunds(rows);
    } catch (err) {
      if (seq === refundSeq.current) {
        setError(errorMessage(err));
      }
    }
  }, [refundStatus]);

  useEffect(() => {
    void loadRefunds();
  }, [loadRefunds]);

  const settleRefund = async (
    refund: RefundSettlementRecord,
    succeeded: boolean,
  ) => {
    const failureReason = (
      refundFailureReasons[refund.id] ?? ""
    ).trim();
    if (!succeeded && !failureReason) {
      setError("标记渠道失败需填写原因");
      return;
    }
    setRefundBusyId(refund.id);
    setRefundNotice("");
    setError("");
    try {
      await api(`/api/admin/finance/refunds/${refund.id}/settle`, {
        method: "POST",
        body: JSON.stringify({
          succeeded,
          reference: (refundReferences[refund.id] ?? "").trim(),
          failure_reason: failureReason,
        }),
      });
      setRefundNotice(
        succeeded
          ? `退款 #${refund.id} 已标记渠道到账`
          : `退款 #${refund.id} 已标记渠道失败`,
      );
      setRefundReferences((previous) => ({
        ...previous,
        [refund.id]: "",
      }));
      setRefundFailureReasons((previous) => ({
        ...previous,
        [refund.id]: "",
      }));
      await loadRefunds();
      await load();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setRefundBusyId(null);
    }
  };

  const retryRefund = async (refund: RefundSettlementRecord) => {
    setRefundBusyId(refund.id);
    setRefundNotice("");
    setError("");
    try {
      await api(`/api/admin/finance/refunds/${refund.id}/retry`, {
        method: "POST",
      });
      setRefundNotice(`退款 #${refund.id} 已重新提交渠道`);
      await loadRefunds();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setRefundBusyId(null);
    }
  };

  const loadInvoices = useCallback(async () => {
    const seq = ++invoiceSeq.current;
    try {
      const rows = await api<InvoiceView[]>(
        `/api/admin/invoices?status=${invoiceStatus}`,
      );
      if (seq === invoiceSeq.current) setInvoices(rows);
    } catch (err) {
      if (seq === invoiceSeq.current) {
        setError(errorMessage(err));
      }
    }
  }, [invoiceStatus]);

  useEffect(() => {
    void loadInvoices();
  }, [loadInvoices]);

  const invoiceAction = async (
    invoice: InvoiceView,
    action: "issue" | "reject" | "void",
  ) => {
    setInvoiceBusyId(invoice.id);
    setInvoiceNotice("");
    setError("");
    try {
      const body =
        action === "issue"
          ? { invoice_no: invoiceNumbers[invoice.id] ?? "" }
          : { reason: invoiceReasons[invoice.id] ?? "" };
      await api(`/api/admin/invoices/${invoice.id}/${action}`, {
        method: "POST",
        body: JSON.stringify(body),
      });
      setInvoiceNotice(
        action === "issue"
          ? `发票 #${invoice.id} 已开具`
          : action === "void"
            ? `发票 #${invoice.id} 已作废`
            : `发票 #${invoice.id} 已驳回`,
      );
      await loadInvoices();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setInvoiceBusyId(null);
    }
  };

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [summaryData, chargeData] = await Promise.all([
        api<FinanceSummary>(`/api/admin/finance?days=${days}`),
        api<AfterSalesChargeLedger>(
          `/api/admin/finance/after-sales-charges?days=${days}&limit=100`,
        ),
      ]);
      setSummary(summaryData);
      setCharges(chargeData);
      setError("");
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setLoading(false);
    }
  }, [days]);

  useEffect(() => {
    void load();
  }, [load]);

  const exportLedger = () => {
    if (!summary) return;
    const header = "时间,类型,订单号,金额";
    const rows = [
      ...summary.ledger.map((row) =>
        [
          row.at,
          KIND_LABELS[row.kind] ?? row.kind,
          row.order_no,
          row.amount_display,
        ].join(","),
      ),
      ...(charges?.items ?? []).map((row) =>
        [
          row.created_at,
          KIND_LABELS[row.kind] ?? row.kind,
          row.order_no,
          row.amount_display,
        ].join(","),
      ),
    ];
    const blob = new Blob([`\ufeff${[header, ...rows].join("\n")}`], {
      type: "text/csv;charset=utf-8",
    });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `afterops-finance-${days}d.csv`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
  };

  return (
    <section className="admin-finance">
      <div className="fulfillment-toolbar">
        <select
          value={days}
          onChange={(event) => setDays(event.target.value)}
          aria-label="财务统计周期"
          data-testid="finance-days"
        >
          <option value="7">近 7 天</option>
          <option value="30">近 30 天</option>
          <option value="90">近 90 天</option>
        </select>
        <button
          className="btn btn-ghost btn-sm"
          type="button"
          onClick={() => void load()}
        >
          刷新
        </button>
        <button
          className="btn btn-ghost btn-sm"
          type="button"
          onClick={exportLedger}
          data-testid="finance-export"
          disabled={!summary}
        >
          导出流水 CSV
        </button>
        <span className="muted">口径：支付毛额 - 已执行退款；补偿单列</span>
      </div>

      {error ? <div className="notice notice-error">{error}</div> : null}
      {loading ? (
        <div className="loading">加载财务数据…</div>
      ) : !summary ? null : (
        <>
          <div className="overview-grid">
            <div className="overview-card">
              <span className="overview-label">支付</span>
              <strong data-testid="finance-paid-amount">
                {summary.paid_amount_display} 元
              </strong>
              <small>{summary.paid_count} 笔</small>
            </div>
            <div className="overview-card">
              <span className="overview-label">退款</span>
              <strong data-testid="finance-refund-amount">
                {summary.refund_amount_display} 元
              </strong>
              <small>{summary.refund_count} 笔</small>
            </div>
            <div className="overview-card">
              <span className="overview-label">补偿</span>
              <strong>{summary.compensation_amount_display} 元</strong>
              <small>{summary.compensation_count} 笔</small>
            </div>
            <div className="overview-card">
              <span className="overview-label">净额</span>
              <strong>{summary.net_amount_display} 元</strong>
              <small>支付 − 退款</small>
            </div>
            <div className="overview-card">
              <span className="overview-label">待审资金提案</span>
              <strong data-testid="finance-pending">
                {summary.pending_proposal_count}
              </strong>
              <small>{summary.pending_proposal_amount_display} 元</small>
            </div>
          </div>

          <div className="overview-section">
            <div className="section-head" style={{ marginBottom: 8 }}>
              <h3>对账异常</h3>
              <StatusBadge
                label={
                  summary.anomalies.length === 0
                    ? "未发现异常"
                    : `发现 ${summary.anomalies.length} 项`
                }
                tone={
                  summary.anomalies.length === 0 ? "badge-ok" : "badge-danger"
                }
              />
            </div>
            {summary.anomalies.length === 0 ? (
              <div className="muted">订单/支付/退款账本一致。</div>
            ) : (
              <div className="proposal-meta">
                {summary.anomalies.map((item) => (
                  <span key={`${item.order_no}-${item.kind}`}>
                    {item.order_no} · {item.kind} · {item.detail}
                  </span>
                ))}
              </div>
            )}
          </div>

          <div className="overview-section">
            <h3>最近流水</h3>
            <div className="trend-table">
              <div className="trend-row trend-head">
                <span>时间</span>
                <span>类型</span>
                <span>订单</span>
                <span>金额</span>
              </div>
              {summary.ledger.map((row) => (
                <div
                  className="trend-row"
                  key={`${row.at}-${row.kind}-${row.order_no}`}
                >
                  <span>{formatDateTime(row.at)}</span>
                  <span>{KIND_LABELS[row.kind] ?? row.kind}</span>
                  <span>{row.order_no}</span>
                  <span>{row.amount_display} 元</span>
                </div>
              ))}
            </div>
          </div>
        </>
      )}

      <div className="overview-section" data-testid="after-sales-charges">
        <div className="section-head" style={{ marginBottom: 8 }}>
          <div>
            <h3 style={{ margin: 0 }}>售后收款</h3>
            <p>
              换货补差与维修费进入售后台账；只记录收款，不改变订单应付。
            </p>
          </div>
          <StatusBadge
            label={`${charges?.count ?? 0} 笔`}
            tone={charges && charges.count > 0 ? "badge-ok" : "badge-neutral"}
          />
        </div>
        {charges ? (
          <>
            <div className="audit-summary">
              <div className="handoff-counts">
                <span data-testid="charge-total-supplement">
                  换货补差 {charges.exchange_supplement_display} 元
                </span>
                <span data-testid="charge-total-repair">
                  维修费 {charges.repair_fee_display} 元
                </span>
                <span data-testid="charge-total-repair-cost">
                  维修成本 {charges.repair_parts_cost_display} 元
                </span>
                <span data-testid="charge-total-repair-margin">
                  维修毛利 {charges.repair_margin_display} 元
                </span>
                <span>合计 {charges.total_display} 元</span>
              </div>
              <span className="muted">
                统计周期 {charges.days} 天；退款见下方渠道结算
              </span>
            </div>
            {charges.items.length === 0 ? (
              <div className="empty-state">
                当前周期没有补差或维修费收款。
              </div>
            ) : (
              <div className="admin-grid">
                {charges.items.map((charge) => (
                  <article
                    className="proposal-card"
                    key={charge.id}
                    data-testid={`after-sales-charge-${charge.id}`}
                  >
                    <div className="proposal-head">
                      <div className="proposal-title">
                        <strong>
                          {KIND_LABELS[charge.kind] ?? charge.kind}
                        </strong>
                        <StatusBadge
                          label={charge.order_no}
                          tone="badge-neutral"
                        />
                      </div>
                      <span className="muted">
                        {charge.amount_display} 元
                      </span>
                    </div>
                    <div className="proposal-meta">
                      <span>顾客 {charge.customer_phone ?? "—"}</span>
                      {charge.return_request_id ? (
                        <span>退货 #{charge.return_request_id}</span>
                      ) : null}
                      {charge.repair_request_id ? (
                        <span>维修 #{charge.repair_request_id}</span>
                      ) : null}
                      <span>收款单 {charge.reference}</span>
                      <span>{formatDateTime(charge.created_at)}</span>
                    </div>
                  </article>
                ))}
              </div>
            )}
          </>
        ) : (
          <div className="loading">加载售后收款…</div>
        )}
      </div>

      <div className="overview-section" data-testid="refund-settlement-queue">
        <div className="section-head" style={{ marginBottom: 8 }}>
          <div>
            <h3 style={{ margin: 0 }}>退款渠道结算</h3>
            <p data-testid="refund-auto-settle-hint">
              {summary && summary.refund_auto_settle_minutes > 0
                ? `处理中超过 ${summary.refund_auto_settle_minutes} 分钟将自动标记渠道到账；`
                : "自动结算已关闭；"}
              {summary &&
              summary.refund_auto_retry_minutes > 0 &&
              summary.refund_auto_retry_max_attempts > 0
                ? `失败超过 ${summary.refund_auto_retry_minutes} 分钟将自动重试，最多 ${summary.refund_auto_retry_max_attempts} 次；`
                : "自动重试已关闭；"}
              仍可手动标记到账或失败。
            </p>
          </div>
          <select
            value={refundStatus}
            onChange={(event) => setRefundStatus(event.target.value)}
            aria-label="按退款结算状态筛选"
            data-testid="refund-settlement-filter"
          >
            <option value="all">全部</option>
            <option value="processing">处理中</option>
            <option value="failed">渠道失败</option>
            <option value="succeeded">已到账</option>
          </select>
        </div>
        {refundNotice ? (
          <div className="notice notice-success">{refundNotice}</div>
        ) : null}
        {refunds.length === 0 ? (
          <div className="empty-state">当前筛选下没有退款结算记录。</div>
        ) : (
          <div className="admin-grid">
            {refunds.map((refund) => (
              <article
                className="proposal-card"
                key={refund.id}
                data-testid={`refund-settlement-${refund.id}`}
              >
                <div className="proposal-head">
                  <div className="proposal-title">
                    <strong>
                      退款 #{refund.id} · {refund.order_no}
                    </strong>
                    <StatusBadge
                      label={settlementLabel(refund.settlement_status)}
                      tone={settlementTone(refund.settlement_status)}
                    />
                    {refund.kind === "compensation" ? (
                      <StatusBadge label="补偿" tone="badge-neutral" />
                    ) : null}
                  </div>
                  <span className="muted">
                    {refund.amount_display} 元 · 尝试{" "}
                    {refund.settlement_attempts} 次
                  </span>
                </div>
                <div className="proposal-meta">
                  <span>原因 {refund.reason || "—"}</span>
                  <span>
                    执行于{" "}
                    {formatDateTime(
                      refund.executed_at ?? refund.created_at,
                    )}
                  </span>
                  {refund.settlement_reference ? (
                    <span>渠道引用 {refund.settlement_reference}</span>
                  ) : null}
                  {refund.settled_at ? (
                    <span>到账于 {formatDateTime(refund.settled_at)}</span>
                  ) : null}
                </div>
                {refund.settlement_failure_reason ? (
                  <div className="notice notice-warn">
                    失败原因：{refund.settlement_failure_reason}
                  </div>
                ) : null}
                {role === "owner" &&
                refund.settlement_status === "processing" ? (
                  <div className="proposal-actions">
                    <input
                      value={refundReferences[refund.id] ?? ""}
                      onChange={(event) =>
                        setRefundReferences((previous) => ({
                          ...previous,
                          [refund.id]: event.target.value,
                        }))
                      }
                      placeholder="渠道引用（可选）"
                      maxLength={128}
                      data-testid={`refund-settlement-reference-${refund.id}`}
                    />
                    <button
                      className="btn btn-primary btn-sm"
                      type="button"
                      data-testid={`refund-settlement-success-${refund.id}`}
                      disabled={refundBusyId === refund.id}
                      onClick={() => void settleRefund(refund, true)}
                    >
                      标记渠道到账
                    </button>
                    <input
                      value={refundFailureReasons[refund.id] ?? ""}
                      onChange={(event) =>
                        setRefundFailureReasons((previous) => ({
                          ...previous,
                          [refund.id]: event.target.value,
                        }))
                      }
                      placeholder="渠道失败原因"
                      maxLength={1000}
                      data-testid={`refund-settlement-failure-${refund.id}`}
                    />
                    <button
                      className="btn btn-danger btn-sm"
                      type="button"
                      data-testid={`refund-settlement-fail-${refund.id}`}
                      disabled={refundBusyId === refund.id}
                      onClick={() => void settleRefund(refund, false)}
                    >
                      标记渠道失败
                    </button>
                  </div>
                ) : null}
                {role === "owner" &&
                refund.settlement_status === "failed" ? (
                  <div className="proposal-actions">
                    <button
                      className="btn btn-primary btn-sm"
                      type="button"
                      data-testid={`refund-settlement-retry-${refund.id}`}
                      disabled={refundBusyId === refund.id}
                      onClick={() => void retryRefund(refund)}
                    >
                      重新提交渠道
                    </button>
                  </div>
                ) : null}
                {role !== "owner" ? (
                  <p className="muted">
                    客服账号只能查看退款结算状态，处理需要店主权限。
                  </p>
                ) : null}
              </article>
            ))}
          </div>
        )}
      </div>

      <div className="overview-section" data-testid="invoice-queue">
        <div className="section-head" style={{ marginBottom: 8 }}>
          <div>
            <h3 style={{ margin: 0 }}>发票申请</h3>
            <p>
              开票只记录发票号，不接税务系统；驳回或作废会通知顾客；
              订单退款后发票金额与净支付不一致时标记需重开。
            </p>
          </div>
          <select
            value={invoiceStatus}
            onChange={(event) => setInvoiceStatus(event.target.value)}
            aria-label="按发票状态筛选"
            data-testid="invoice-filter"
          >
            <option value="requested">待开票</option>
            <option value="issued">已开票</option>
            <option value="needs_adjustment">需重开</option>
            <option value="rejected">已驳回</option>
            <option value="voided">已作废</option>
            <option value="all">全部</option>
          </select>
        </div>
        {invoiceNotice ? (
          <div className="notice notice-success">{invoiceNotice}</div>
        ) : null}
        {role !== "owner" ? (
          <div className="notice notice-warn">
            客服账号只读；开票、作废和驳回需要店主权限。
          </div>
        ) : null}
        {invoices.length === 0 ? (
          <div className="empty-state">当前筛选下没有发票申请。</div>
        ) : (
          <div className="admin-grid">
            {invoices.map((invoice) => (
              <article
                className="proposal-card"
                key={invoice.id}
                data-testid={`invoice-${invoice.id}`}
              >
                <div className="proposal-head">
                  <div className="proposal-title">
                    <strong>
                      发票 #{invoice.id} · {invoice.order_no}
                    </strong>
                    <StatusBadge
                      label={invoiceStatusLabel(invoice.status)}
                      tone={
                        invoice.status === "issued"
                          ? "badge-ok"
                          : invoice.status === "rejected" ||
                              invoice.status === "voided"
                            ? "badge-muted"
                            : "badge-warn"
                      }
                    />
                    {invoice.adjustment_required ? (
                      <span className="badge badge-warn">需重开</span>
                    ) : null}
                  </div>
                  <span className="muted">
                    {formatDateTime(invoice.created_at)}
                  </span>
                </div>
                <div className="proposal-meta">
                  <span>
                    {invoice.invoice_type === "company" ? "企业" : "个人"} ·{" "}
                    {invoice.title}
                  </span>
                  <span>
                    {(invoice.amount_cents / 100).toFixed(2)} 元
                  </span>
                  <span>
                    净支付 {(invoice.net_paid_cents / 100).toFixed(2)} 元
                  </span>
                  {invoice.tax_no ? (
                    <span>税号 {invoice.tax_no}</span>
                  ) : null}
                  {invoice.email ? <span>{invoice.email}</span> : null}
                  {invoice.invoice_no ? (
                    <span>发票号 {invoice.invoice_no}</span>
                  ) : null}
                  {invoice.reject_reason ? (
                    <span>驳回：{invoice.reject_reason}</span>
                  ) : null}
                  {invoice.void_reason ? (
                    <span>作废：{invoice.void_reason}</span>
                  ) : null}
                </div>
                {role === "owner" && invoice.status === "requested" ? (
                  <div className="proposal-actions">
                    <input
                      value={invoiceNumbers[invoice.id] ?? ""}
                      onChange={(event) =>
                        setInvoiceNumbers((previous) => ({
                          ...previous,
                          [invoice.id]: event.target.value,
                        }))
                      }
                      placeholder="发票号"
                      maxLength={64}
                      data-testid={`invoice-no-${invoice.id}`}
                    />
                    <button
                      className="btn btn-primary btn-sm"
                      type="button"
                      data-testid={`issue-invoice-${invoice.id}`}
                      disabled={
                        invoiceBusyId === invoice.id ||
                        !(invoiceNumbers[invoice.id] ?? "").trim()
                      }
                      onClick={() => void invoiceAction(invoice, "issue")}
                    >
                      开票
                    </button>
                    <input
                      value={invoiceReasons[invoice.id] ?? ""}
                      onChange={(event) =>
                        setInvoiceReasons((previous) => ({
                          ...previous,
                          [invoice.id]: event.target.value,
                        }))
                      }
                      placeholder="驳回原因"
                      maxLength={1000}
                    />
                    <button
                      className="btn btn-danger btn-sm"
                      type="button"
                      data-testid={`reject-invoice-${invoice.id}`}
                      disabled={invoiceBusyId === invoice.id}
                      onClick={() => void invoiceAction(invoice, "reject")}
                    >
                      驳回
                    </button>
                  </div>
                ) : null}
                {role === "owner" && invoice.status === "issued" ? (
                  <div className="proposal-actions">
                    <input
                      value={invoiceReasons[invoice.id] ?? ""}
                      onChange={(event) =>
                        setInvoiceReasons((previous) => ({
                          ...previous,
                          [invoice.id]: event.target.value,
                        }))
                      }
                      placeholder="作废原因"
                      maxLength={500}
                      data-testid={`void-reason-${invoice.id}`}
                    />
                    <button
                      className="btn btn-danger btn-sm"
                      type="button"
                      data-testid={`void-invoice-${invoice.id}`}
                      disabled={
                        invoiceBusyId === invoice.id ||
                        !(invoiceReasons[invoice.id] ?? "").trim()
                      }
                      onClick={() => void invoiceAction(invoice, "void")}
                    >
                      作废发票
                    </button>
                  </div>
                ) : null}
              </article>
            ))}
          </div>
        )}
      </div>
    </section>
  );
}
