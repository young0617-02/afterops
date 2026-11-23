"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { StatusBadge } from "@/components/StatusBadge";
import { useCustomer } from "@/components/CustomerProvider";
import { api, errorMessage } from "@/lib/api";
import type { ClaimableCoupon, WalletCoupon } from "@/lib/types";

function valueLabel(coupon: ClaimableCoupon): string {
  return coupon.discount_type === "fixed"
    ? `减 ${(coupon.value / 100).toFixed(2)} 元`
    : `减 ${coupon.value}%`;
}

function scopeLabel(coupon: ClaimableCoupon): string {
  if (coupon.scope_type === "category") {
    return `限类目：${coupon.scope_values.join("、")}`;
  }
  if (coupon.scope_type === "sku") {
    return `限 SKU：${coupon.scope_values.join("、")}`;
  }
  return "全场商品";
}

function walletStateLabel(state: string): string {
  if (state === "active") return "可使用";
  if (state === "used_up") return "已用完";
  if (state === "expired") return "已过期";
  return "已停用";
}

function walletTone(state: string): string {
  if (state === "active") return "badge-ok";
  return "badge-muted";
}

export default function CouponsPage() {
  const { customer } = useCustomer();
  const [claimable, setClaimable] = useState<ClaimableCoupon[]>([]);
  const [wallet, setWallet] = useState<WalletCoupon[]>([]);
  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const load = useCallback(async () => {
    if (!customer) {
      setClaimable([]);
      setWallet([]);
      setLoading(false);
      return;
    }
    setLoading(true);
    try {
      const [available, mine] = await Promise.all([
        api<ClaimableCoupon[]>("/api/store/coupons/claimable"),
        api<WalletCoupon[]>("/api/store/coupons/wallet"),
      ]);
      setClaimable(available);
      setWallet(mine);
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

  const claim = async (coupon: ClaimableCoupon) => {
    setBusyId(coupon.id);
    setError("");
    setNotice("");
    try {
      const claimed = await api<WalletCoupon>(
        `/api/store/coupons/${coupon.id}/claim`,
        { method: "POST" },
      );
      setNotice(`已领取 ${claimed.code}，可在结算时使用`);
      await load();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusyId(null);
    }
  };

  if (!customer) {
    return (
      <section className="panel panel-pad">
        <h1>领券中心</h1>
        <p className="muted">请先在右上角用手机号登录，再领取优惠券。</p>
      </section>
    );
  }

  return (
    <section className="coupons-page">
      <div className="section-head">
        <div>
          <h1 style={{ margin: 0 }}>领券中心</h1>
          <p>领取后可在「我的券包」查看优惠码，结算时输入使用。</p>
        </div>
        <button
          className="btn btn-ghost btn-sm"
          type="button"
          onClick={() => void load()}
          data-testid="refresh-coupons"
        >
          刷新
        </button>
      </div>
      {notice ? (
        <div className="notice notice-success" data-testid="coupon-notice">
          {notice}
        </div>
      ) : null}
      {error ? <div className="notice notice-error">{error}</div> : null}
      {loading ? (
        <div className="loading">加载优惠券…</div>
      ) : (
        <>
          <h2>可领取</h2>
          {claimable.length === 0 ? (
            <div className="empty-state">当前没有可领取的优惠券。</div>
          ) : (
            <div className="admin-grid">
              {claimable.map((coupon) => (
                <article
                  className="proposal-card"
                  key={coupon.id}
                  data-testid={`claimable-coupon-${coupon.id}`}
                >
                  <div className="proposal-head">
                    <div className="proposal-title">
                      <strong>{valueLabel(coupon)}</strong>
                      <span className="badge badge-neutral">
                        {scopeLabel(coupon)}
                      </span>
                    </div>
                    <span className="muted">
                      至 {coupon.ends_on ?? "长期"}
                    </span>
                  </div>
                  <div className="proposal-meta">
                    <span>
                      门槛{" "}
                      {(coupon.min_spend_cents / 100).toFixed(2)} 元
                    </span>
                    <span>
                      有效期 {coupon.starts_on} 至{" "}
                      {coupon.ends_on ?? "长期"}
                    </span>
                  </div>
                  <div className="proposal-actions">
                    <button
                      className="btn btn-primary btn-sm"
                      type="button"
                      data-testid={`claim-coupon-${coupon.id}`}
                      disabled={
                        coupon.claimed ||
                        coupon.sold_out ||
                        busyId === coupon.id
                      }
                      onClick={() => void claim(coupon)}
                    >
                      {coupon.claimed
                        ? "已领取"
                        : coupon.sold_out
                          ? "已领完"
                          : busyId === coupon.id
                            ? "领取中"
                            : "领取"}
                    </button>
                  </div>
                </article>
              ))}
            </div>
          )}

          <h2 style={{ marginTop: 20 }}>我的券包</h2>
          {wallet.length === 0 ? (
            <div className="empty-state">还没有领取优惠券。</div>
          ) : (
            <div className="admin-grid">
              {wallet.map((coupon) => (
                <article
                  className="proposal-card"
                  key={coupon.id}
                  data-testid={`wallet-coupon-${coupon.id}`}
                >
                  <div className="proposal-head">
                    <div className="proposal-title">
                      <strong className="coupon-code">{coupon.code}</strong>
                      <span className="badge badge-neutral">
                        {valueLabel(coupon)}
                      </span>
                      <StatusBadge
                        label={walletStateLabel(coupon.state)}
                        tone={walletTone(coupon.state)}
                      />
                    </div>
                    <span className="muted">
                      领取于 {coupon.claimed_at.slice(0, 10)}
                    </span>
                  </div>
                  <div className="proposal-meta">
                    <span>
                      门槛{" "}
                      {(coupon.min_spend_cents / 100).toFixed(2)} 元
                    </span>
                    <span>
                      有效期 {coupon.starts_on} 至{" "}
                      {coupon.ends_on ?? "长期"}
                    </span>
                    <span>
                      已用 {coupon.times_used} / 每客限用{" "}
                      {coupon.times_used + coupon.remaining_uses}
                    </span>
                    <span>{scopeLabel(coupon)}</span>
                  </div>
                  {coupon.state === "active" ? (
                    <div className="proposal-actions">
                      <Link className="btn btn-ghost btn-sm" href="/cart">
                        去结算使用
                      </Link>
                    </div>
                  ) : null}
                </article>
              ))}
            </div>
          )}
        </>
      )}
    </section>
  );
}
