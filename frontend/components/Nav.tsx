"use client";

import Link from "next/link";

export function Nav() {
  return (
    <div className="nav">
      <Link className="brand" href="/">
        <span className="brand-mark">A</span>
        <span className="brand-text">
          <strong>AfterOps</strong>
          <small>AI 售后运营台</small>
        </span>
      </Link>
      <nav className="nav-links" aria-label="主导航">
        <Link href="/">店面</Link>
        <Link href="/cart">购物车</Link>
        <Link href="/coupons">领券中心</Link>
        <Link href="/orders">我的订单</Link>
        <Link href="/addresses">地址簿</Link>
        <Link href="/after-sales">售后进度</Link>
        <Link href="/help">帮助</Link>
        <Link href="/account">账户</Link>
        <Link href="/admin">商家后台</Link>
      </nav>
    </div>
  );
}
