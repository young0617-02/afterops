import type { Metadata } from "next";
import type { ReactNode } from "react";

import { ChatWidget } from "@/components/ChatWidget";
import { CustomerProvider } from "@/components/CustomerProvider";
import { Nav } from "@/components/Nav";
import { SessionBar } from "@/components/SessionBar";

import "./globals.css";

export const metadata: Metadata = {
  title: "AfterOps · AI 售后运营台",
  description: "3C 配件售后运营：订单、物流、政策、提案与人工审批。",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="zh-CN">
      <body>
        <CustomerProvider>
          <header className="site-header">
            <Nav />
            <SessionBar />
          </header>
          <main className="page">{children}</main>
          <footer className="site-footer">
            <span>AfterOps · 售后有依据，资金有审批</span>
            <span>交易有据 · 审批留痕</span>
          </footer>
          <ChatWidget />
        </CustomerProvider>
      </body>
    </html>
  );
}
