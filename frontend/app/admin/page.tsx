"use client";

import { useCallback, useEffect, useState, type FormEvent } from "react";

import { AdminAudit } from "@/components/AdminAudit";
import { AdminCoupons } from "@/components/AdminCoupons";
import { AdminCustomers } from "@/components/AdminCustomers";
import { AdminFinance } from "@/components/AdminFinance";
import { AdminFulfillment } from "@/components/AdminFulfillment";
import { AdminHandoff } from "@/components/AdminHandoff";
import { AdminManualAction } from "@/components/AdminManualAction";
import { AdminOutbox } from "@/components/AdminOutbox";
import { AdminOverview } from "@/components/AdminOverview";
import { AdminPolicies } from "@/components/AdminPolicies";
import { AdminProducts } from "@/components/AdminProducts";
import { AdminQuickReplies } from "@/components/AdminQuickReplies";
import { AdminRepairs } from "@/components/AdminRepairs";
import { AdminReturns } from "@/components/AdminReturns";
import { AdminReviews } from "@/components/AdminReviews";
import { AdminShipping } from "@/components/AdminShipping";
import { AdminStaff } from "@/components/AdminStaff";
import { NotificationBell } from "@/components/NotificationBell";
import { StatusBadge } from "@/components/StatusBadge";
import { api, errorMessage } from "@/lib/api";
import {
  actionTypeLabel,
  formatDateTime,
  proposalStatusLabel,
  riskLevelLabel,
} from "@/lib/status";
import type { AdminMe, Proposal } from "@/lib/types";

const FILTERS = [
  { value: "pending", label: "待审批" },
  { value: "executed", label: "已执行" },
  { value: "rejected", label: "已驳回" },
  { value: "", label: "全部" },
];

function riskTone(risk: string): string {
  if (risk === "L2") return "badge-warn";
  if (risk === "L3") return "badge-danger";
  return "badge-neutral";
}

export default function AdminPage() {
  const [admin, setAdmin] = useState<AdminMe | null>(null);
  const [tab, setTab] = useState<
    | "overview"
    | "customers"
    | "finance"
    | "proposals"
    | "handoffs"
    | "returns"
    | "repairs"
    | "fulfillment"
    | "audit"
    | "products"
    | "reviews"
    | "coupons"
    | "shipping"
    | "policies"
    | "outbox"
    | "staff"
    | "quick_replies"
  >("overview");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [proposals, setProposals] = useState<Proposal[]>([]);
  const [filter, setFilter] = useState("pending");
  const [proposalQueryInput, setProposalQueryInput] = useState("");
  const [proposalQuery, setProposalQuery] = useState("");
  const [proposalHasMore, setProposalHasMore] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [rejectReasons, setRejectReasons] = useState<Record<number, string>>({});

  const loadProposals = useCallback(
    async (append = false, beforeId?: number) => {
      if (!admin) return;
      if (append) setLoadingMore(true);
      else setLoading(true);
      try {
        const params = new URLSearchParams({ limit: "50" });
        if (filter) params.set("status", filter);
        if (proposalQuery.trim()) params.set("q", proposalQuery.trim());
        if (append && beforeId) params.set("before_id", String(beforeId));
        const rows = await api<Proposal[]>(
          `/api/admin/proposals?${params.toString()}`,
        );
        if (append) {
          setProposals((previous) => [...previous, ...rows]);
        } else {
          setProposals(rows);
        }
        setProposalHasMore(rows.length === 50);
        setError("");
      } catch (err) {
        setError(errorMessage(err));
      } finally {
        if (append) setLoadingMore(false);
        else setLoading(false);
      }
    },
    [admin, filter, proposalQuery],
  );

  useEffect(() => {
    let cancelled = false;
    api<AdminMe>("/api/admin/me")
      .then((me) => {
        if (!cancelled) setAdmin(me);
      })
      .catch(() => {
        if (!cancelled) setAdmin(null);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    void loadProposals();
  }, [loadProposals]);

  useEffect(() => {
    if (!admin) return;
    const send = () => {
      void api("/api/admin/heartbeat", { method: "POST" }).catch(() => null);
    };
    send();
    const timer = window.setInterval(send, 30_000);
    const onVisible = () => {
      if (document.visibilityState === "visible") send();
    };
    document.addEventListener("visibilitychange", onVisible);
    window.addEventListener("focus", send);
    return () => {
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisible);
      window.removeEventListener("focus", send);
    };
  }, [admin?.user_id]);

  const login = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const me = await api<AdminMe>("/api/admin/session", {
        method: "POST",
        body: JSON.stringify({ email, password }),
      });
      setAdmin(me);
      setPassword("");
      setNotice("已登录商家后台");
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  const logout = async () => {
    await api("/api/admin/session", { method: "DELETE" }).catch(() => null);
    setAdmin(null);
    setProposals([]);
    setNotice("");
  };

  const toggleAvailability = async () => {
    if (!admin) return;
    try {
      const updated = await api<AdminMe>("/api/admin/me/availability", {
        method: "PATCH",
        body: JSON.stringify({
          available: !admin.handoff_available,
        }),
      });
      setAdmin(updated);
      setNotice(
        updated.handoff_available ? "已切换为接单中" : "已切换为离开",
      );
    } catch (err) {
      setError(errorMessage(err));
    }
  };

  const approve = async (proposal: Proposal) => {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const result = await api<{ status: string; commerce_ref: string | null }>(
        `/api/admin/proposals/${proposal.id}/approve`,
        { method: "POST" },
      );
      setNotice(
        `提案 #${proposal.id} 已执行${result.commerce_ref ? ` · ${result.commerce_ref}` : ""}`,
      );
      await loadProposals();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  const reject = async (proposal: Proposal) => {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await api(`/api/admin/proposals/${proposal.id}/reject`, {
        method: "POST",
        body: JSON.stringify({ reason: rejectReasons[proposal.id] ?? "" }),
      });
      setNotice(`提案 #${proposal.id} 已驳回`);
      await loadProposals();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  if (loading) return <div className="loading">检查管理端会话…</div>;

  if (!admin) {
    return (
      <section className="panel admin-login">
        <h1>商家后台</h1>
        <p>使用店主或客服账号登录，处理 AI 提交的售后提案。</p>
        {error ? <div className="notice notice-error">{error}</div> : null}
        <form onSubmit={login}>
          <div className="field" style={{ marginBottom: 10 }}>
            <label htmlFor="admin-email">邮箱</label>
            <input
              id="admin-email"
              type="email"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              autoComplete="username"
              required
            />
          </div>
          <div className="field" style={{ marginBottom: 14 }}>
            <label htmlFor="admin-password">密码</label>
            <input
              id="admin-password"
              type="password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              autoComplete="current-password"
              required
              minLength={8}
            />
          </div>
          <button
            className="btn btn-primary btn-block"
            type="submit"
            data-testid="admin-login"
            disabled={busy}
          >
            {busy ? "登录中" : "登录"}
          </button>
        </form>
      </section>
    );
  }

  return (
    <div className="admin-shell">
      <div className="section-head">
        <div>
          <h2>售后运营台</h2>
          <p data-testid="admin-session-info">
            当前登录：{admin.email} ·{" "}
            {admin.role === "owner" ? "店主" : "客服"}
          </p>
        </div>
        <div className="card-actions">
          {tab === "proposals" ? (
            <>
              <form
                className="proposal-search"
                onSubmit={(event) => {
                  event.preventDefault();
                  setProposalQuery(proposalQueryInput);
                }}
              >
                <input
                  value={proposalQueryInput}
                  onChange={(event) =>
                    setProposalQueryInput(event.target.value)
                  }
                  placeholder="搜索订单号或提案号"
                  maxLength={100}
                  aria-label="搜索提案"
                  data-testid="proposal-search"
                />
                <button className="btn btn-ghost btn-sm" type="submit">
                  搜索
                </button>
              </form>
              <select
                value={filter}
                onChange={(event) => setFilter(event.target.value)}
                aria-label="按状态筛选"
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
                onClick={() => void loadProposals()}
              >
                刷新
              </button>
            </>
          ) : null}
          <NotificationBell scope="admin" />
          <button
            className="btn btn-ghost btn-sm"
            type="button"
            data-testid="handoff-availability"
            onClick={() => void toggleAvailability()}
          >
            {admin.handoff_available ? "接单中" : "已离开"}
          </button>
          <button
            className="btn btn-ghost btn-sm"
            type="button"
            data-testid="admin-logout"
            onClick={() => void logout()}
          >
            退出
          </button>
        </div>
      </div>

      <div className="admin-tabs" role="tablist" aria-label="后台功能区">
        <button
          className={`admin-tab ${tab === "overview" ? "active" : ""}`}
          type="button"
          role="tab"
          aria-selected={tab === "overview"}
          data-testid="tab-overview"
          onClick={() => {
            setTab("overview");
            setNotice("");
            setError("");
          }}
        >
          运营概览
        </button>
        <button
          className={`admin-tab ${tab === "customers" ? "active" : ""}`}
          type="button"
          role="tab"
          aria-selected={tab === "customers"}
          data-testid="tab-customers"
          onClick={() => {
            setTab("customers");
            setNotice("");
            setError("");
          }}
        >
          客户
        </button>
        <button
          className={`admin-tab ${tab === "finance" ? "active" : ""}`}
          type="button"
          role="tab"
          aria-selected={tab === "finance"}
          data-testid="tab-finance"
          onClick={() => {
            setTab("finance");
            setNotice("");
            setError("");
          }}
        >
          财务
        </button>
        <button
          className={`admin-tab ${tab === "proposals" ? "active" : ""}`}
          type="button"
          role="tab"
          aria-selected={tab === "proposals"}
          data-testid="tab-proposals"
          onClick={() => {
            setTab("proposals");
            setNotice("");
            setError("");
            void loadProposals();
          }}
        >
          售后提案
        </button>
        <button
          className={`admin-tab ${tab === "handoffs" ? "active" : ""}`}
          type="button"
          role="tab"
          aria-selected={tab === "handoffs"}
          data-testid="tab-handoffs"
          onClick={() => {
            setTab("handoffs");
            setNotice("");
            setError("");
          }}
        >
          人工工单
        </button>
        <button
          className={`admin-tab ${tab === "returns" ? "active" : ""}`}
          type="button"
          role="tab"
          aria-selected={tab === "returns"}
          data-testid="tab-returns"
          onClick={() => {
            setTab("returns");
            setNotice("");
            setError("");
          }}
        >
          退货
        </button>
        <button
          className={`admin-tab ${tab === "repairs" ? "active" : ""}`}
          type="button"
          role="tab"
          aria-selected={tab === "repairs"}
          data-testid="tab-repairs"
          onClick={() => {
            setTab("repairs");
            setNotice("");
            setError("");
          }}
        >
          维修
        </button>
        <button
          className={`admin-tab ${tab === "fulfillment" ? "active" : ""}`}
          type="button"
          role="tab"
          aria-selected={tab === "fulfillment"}
          data-testid="tab-fulfillment"
          onClick={() => {
            setTab("fulfillment");
            setNotice("");
            setError("");
          }}
        >
          订单履约
        </button>
        <button
          className={`admin-tab ${tab === "products" ? "active" : ""}`}
          type="button"
          role="tab"
          aria-selected={tab === "products"}
          data-testid="tab-products"
          onClick={() => {
            setTab("products");
            setNotice("");
            setError("");
          }}
        >
          商品库存
        </button>
        <button
          className={`admin-tab ${tab === "coupons" ? "active" : ""}`}
          type="button"
          role="tab"
          aria-selected={tab === "coupons"}
          data-testid="tab-coupons"
          onClick={() => {
            setTab("coupons");
            setNotice("");
            setError("");
          }}
        >
          优惠券
        </button>
        <button
          className={`admin-tab ${tab === "shipping" ? "active" : ""}`}
          type="button"
          role="tab"
          aria-selected={tab === "shipping"}
          data-testid="tab-shipping"
          onClick={() => {
            setTab("shipping");
            setNotice("");
            setError("");
          }}
        >
          配送
        </button>
        <button
          className={`admin-tab ${tab === "reviews" ? "active" : ""}`}
          type="button"
          role="tab"
          aria-selected={tab === "reviews"}
          data-testid="tab-product-reviews"
          onClick={() => {
            setTab("reviews");
            setNotice("");
            setError("");
          }}
        >
          评价
        </button>
        <button
          className={`admin-tab ${tab === "policies" ? "active" : ""}`}
          type="button"
          role="tab"
          aria-selected={tab === "policies"}
          data-testid="tab-policies"
          onClick={() => {
            setTab("policies");
            setNotice("");
            setError("");
          }}
        >
          政策
        </button>
        <button
          className={`admin-tab ${tab === "outbox" ? "active" : ""}`}
          type="button"
          role="tab"
          aria-selected={tab === "outbox"}
          data-testid="tab-outbox"
          onClick={() => {
            setTab("outbox");
            setNotice("");
            setError("");
          }}
        >
          邮件投递
        </button>
        {admin.role === "owner" ? (
          <button
            className={`admin-tab ${tab === "staff" ? "active" : ""}`}
            type="button"
            role="tab"
            aria-selected={tab === "staff"}
            data-testid="tab-staff"
            onClick={() => {
              setTab("staff");
              setNotice("");
              setError("");
            }}
          >
            团队
          </button>
        ) : null}
        {admin.role === "owner" ? (
          <button
            className={`admin-tab ${tab === "quick_replies" ? "active" : ""}`}
            type="button"
            role="tab"
            aria-selected={tab === "quick_replies"}
            data-testid="tab-quick-replies"
            onClick={() => {
              setTab("quick_replies");
              setNotice("");
              setError("");
            }}
          >
            话术
          </button>
        ) : null}
        <button
          className={`admin-tab ${tab === "audit" ? "active" : ""}`}
          type="button"
          role="tab"
          aria-selected={tab === "audit"}
          data-testid="tab-audit"
          onClick={() => {
            setTab("audit");
            setNotice("");
            setError("");
          }}
        >
          审计链
        </button>
      </div>

      {tab === "overview" ? (
        <AdminOverview
          onNavigate={(next) => {
            setTab(next);
            setNotice("");
            setError("");
          }}
        />
      ) : tab === "customers" ? (
        <AdminCustomers />
      ) : tab === "finance" ? (
        <AdminFinance role={admin.role} />
      ) : tab === "handoffs" ? (
        <AdminHandoff currentUserId={admin.user_id} role={admin.role} />
      ) : tab === "returns" ? (
        <AdminReturns role={admin.role} />
      ) : tab === "repairs" ? (
        <AdminRepairs role={admin.role} />
      ) : tab === "fulfillment" ? (
        <AdminFulfillment role={admin.role} />
      ) : tab === "reviews" ? (
        <AdminReviews role={admin.role} />
      ) : tab === "coupons" ? (
        <AdminCoupons role={admin.role} />
      ) : tab === "shipping" ? (
        <AdminShipping role={admin.role} />
      ) : tab === "policies" ? (
        <AdminPolicies role={admin.role} />
      ) : tab === "outbox" ? (
        <AdminOutbox role={admin.role} />
      ) : tab === "audit" ? (
        <AdminAudit />
      ) : tab === "staff" ? (
        <AdminStaff currentUserId={admin.user_id} />
      ) : tab === "quick_replies" ? (
        <AdminQuickReplies />
      ) : tab === "products" ? (
        <AdminProducts role={admin.role} />
      ) : (
        <>
          {notice ? <div className="notice notice-success">{notice}</div> : null}
          {error ? <div className="notice notice-error">{error}</div> : null}

          {admin.role === "owner" ? (
            <AdminManualAction
              onCreated={(message) => {
                setNotice(message);
                void loadProposals();
              }}
            />
          ) : null}

          {admin.role !== "owner" ? (
        <div className="notice notice-warn">
          当前账号是客服角色，可以接管人工工单；资金/地址提案需要店主审批。
        </div>
      ) : null}

      {loading ? (
        <div className="loading">加载提案…</div>
      ) : proposals.length === 0 ? (
        <div className="empty-state">当前筛选下没有提案。</div>
      ) : (
        <div className="admin-grid">
          {proposals.map((proposal) => (
            <article className="proposal-card" key={proposal.id}>
              <div className="proposal-head">
                <div className="proposal-title">
                  <strong>
                    #{proposal.id} {actionTypeLabel(proposal.action_type)}
                  </strong>
                  <StatusBadge
                    label={proposalStatusLabel(proposal.status)}
                    status={proposal.status}
                  />
                  <StatusBadge
                    label={riskLevelLabel(proposal.risk_level)}
                    tone={riskTone(proposal.risk_level)}
                  />
                </div>
                <span className="muted">{formatDateTime(proposal.created_at)}</span>
              </div>
              <div className="proposal-meta">
                <span>订单 {proposal.order_no ?? "—"}</span>
                <span>
                  金额{" "}
                  {proposal.amount_display ? `${proposal.amount_display} 元` : "—"}
                </span>
                <span>
                  {proposal.created_by === "staff"
                    ? "店主发起"
                    : proposal.created_by === "customer"
                      ? "顾客申请"
                      : "AI 提案"}
                </span>
                {proposal.commerce_ref ? (
                  <span>执行引用 {proposal.commerce_ref}</span>
                ) : null}
              </div>
              {proposal.reason ? (
                <p className="after-sales-reason">{proposal.reason}</p>
              ) : null}
              {proposal.cancel_items.length > 0 ? (
                <div className="proposal-meta">
                  {proposal.cancel_items.map((item) => (
                    <span key={`cancel-item-${item.order_item_id}`}>
                      取消 {item.sku} × {item.qty}
                    </span>
                  ))}
                </div>
              ) : null}
              {proposal.attachment_ids.length > 0 ? (
                <div className="attachment-grid">
                  {proposal.attachment_ids.map((attachmentId) => (
                    <a
                      key={attachmentId}
                      href={`/api/admin/attachments/${attachmentId}`}
                      target="_blank"
                      rel="noreferrer"
                    >
                      <img
                        src={`/api/admin/attachments/${attachmentId}`}
                        alt="申请凭证"
                      />
                    </a>
                  ))}
                </div>
              ) : null}

              {proposal.status === "pending" && admin.role === "owner" ? (
                <div className="proposal-actions">
                  <button
                    className="btn btn-primary"
                    type="button"
                    data-testid={`approve-${proposal.id}`}
                    disabled={busy}
                    onClick={() => void approve(proposal)}
                  >
                    批准并执行
                  </button>
                  <div className="reject-row">
                    <input
                      value={rejectReasons[proposal.id] ?? ""}
                      onChange={(event) =>
                        setRejectReasons((previous) => ({
                          ...previous,
                          [proposal.id]: event.target.value,
                        }))
                      }
                      placeholder="驳回原因（可选）"
                      maxLength={500}
                      aria-label={`提案 ${proposal.id} 驳回原因`}
                    />
                    <button
                      className="btn btn-danger"
                      type="button"
                      disabled={busy}
                      onClick={() => void reject(proposal)}
                    >
                      驳回
                    </button>
                  </div>
                </div>
              ) : null}
            </article>
          ))}
        </div>
      )}
      {tab === "proposals" && proposalHasMore ? (
        <button
          className="btn btn-ghost"
          type="button"
          disabled={loadingMore}
          onClick={() =>
            void loadProposals(true, proposals[proposals.length - 1]?.id)
          }
        >
          {loadingMore ? "加载中" : "加载更多提案"}
        </button>
      ) : null}
        </>
      )}
    </div>
  );
}
