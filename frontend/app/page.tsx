"use client";

import Link from "next/link";
import { useEffect, useState, type FormEvent } from "react";

import { useCustomer } from "@/components/CustomerProvider";
import { api, errorMessage } from "@/lib/api";
import type { CategoryCount, ProductSummary } from "@/lib/types";

export default function StorePage() {
  const { customer } = useCustomer();
  const [products, setProducts] = useState<ProductSummary[]>([]);
  const [categories, setCategories] = useState<CategoryCount[]>([]);
  const [searchInput, setSearchInput] = useState("");
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [noticeIsError, setNoticeIsError] = useState(false);
  const [adding, setAdding] = useState<string | null>(null);

  useEffect(() => {
    api<CategoryCount[]>("/api/store/categories")
      .then(setCategories)
      .catch(() => setCategories([]));
  }, []);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    const params = new URLSearchParams();
    if (query) params.set("q", query);
    if (category) params.set("category", category);
    api<ProductSummary[]>(`/api/store/products?${params.toString()}`)
      .then((data) => {
        if (cancelled) return;
        setProducts(data);
        setError("");
      })
      .catch((err) => {
        if (!cancelled) setError(errorMessage(err));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [query, category]);

  const search = (event: FormEvent) => {
    event.preventDefault();
    setQuery(searchInput.trim());
  };

  const addToCart = async (product: ProductSummary) => {
    if (!customer) {
      setNotice("请先在右上角用手机号登录");
      setNoticeIsError(true);
      return;
    }
    setAdding(product.sku);
    setNotice("");
    try {
      await api("/api/store/cart/items", {
        method: "POST",
        body: JSON.stringify({ sku: product.sku, qty: 1 }),
      });
      setNotice(`${product.title} 已加入购物车`);
      setNoticeIsError(false);
    } catch (err) {
      setNotice(errorMessage(err));
      setNoticeIsError(true);
    } finally {
      setAdding(null);
    }
  };

  return (
    <>
      <section className="hero">
        <div>
          <p className="eyebrow">3C 配件 · 售后运营</p>
          <h1>售后有依据，资金有审批。</h1>
          <p>
            AI 只查订单、物流与政策，只提交退款、补偿、改地址申请；
            资金动作由商家审批后执行，每个结论都有出处和审计记录。
          </p>
        </div>
        <div className="hero-panel">
          <div>
            <strong>事实可查</strong>
            <span>订单、金额、物流、库存直接来自交易系统</span>
          </div>
          <div>
            <strong>提案可审</strong>
            <span>AI 不直接退款，资金动作必须人工批准</span>
          </div>
          <div>
            <strong>全程留痕</strong>
            <span>政策版本、工具结果、审批人与执行结果可追溯</span>
          </div>
        </div>
      </section>

      <section className="section">
        <div className="section-head">
          <div>
            <h2>商品</h2>
            <p>先登录手机号，才能加入购物车和发起售后。</p>
          </div>
          <span className="badge badge-neutral">{products.length} 个在售商品</span>
        </div>

        <div className="toolbar">
          <form className="search-form" onSubmit={search}>
            <input
              value={searchInput}
              onChange={(event) => setSearchInput(event.target.value)}
              placeholder="搜索商品名、品牌、SKU…"
              aria-label="搜索商品"
            />
            <button className="btn btn-ghost" type="submit">
              搜索
            </button>
          </form>
          <div className="chip-row">
            <button
              className={`chip ${category === "" ? "active" : ""}`}
              type="button"
              onClick={() => setCategory("")}
            >
              全部
            </button>
            {categories.map((item) => (
              <button
                key={item.category}
                className={`chip ${category === item.category ? "active" : ""}`}
                type="button"
                onClick={() => setCategory(item.category)}
              >
                {item.category} {item.count}
              </button>
            ))}
          </div>
        </div>

        {notice ? (
          <div className={`notice ${noticeIsError ? "notice-error" : "notice-success"}`}>
            {notice}
          </div>
        ) : null}
        {error ? <div className="notice notice-error">{error}</div> : null}

        {loading ? (
          <div className="loading">加载商品中…</div>
        ) : products.length === 0 ? (
          <div className="empty-state">没有匹配的商品，换个关键词或类目试试。</div>
        ) : (
          <div className="product-grid">
            {products.map((product) => (
              <article className="product-card" key={product.sku}>
                <div className="product-media">
                  {product.image_url ? (
                    <img
                      src={product.image_url}
                      alt={product.title}
                      loading="lazy"
                    />
                  ) : (
                    <span>{product.category || "商品"}</span>
                  )}
                </div>
                <div className="product-meta">
                  <span>{product.category}</span>
                  <span>·</span>
                  <span>{product.brand}</span>
                  {product.variant_label ? (
                    <span>{product.variant_label}</span>
                  ) : null}
                  {product.support_7d_no_reason ? (
                    <span className="badge badge-ok">7 天无理由</span>
                  ) : null}
                </div>
                <h3 className="product-title">
                  <Link href={`/product/${product.sku}`}>{product.title}</Link>
                </h3>
                <div className="price-row">
                  <span className="price">
                    {product.price_display}
                    <small>元</small>
                  </span>
                  <span className={product.in_stock ? "stock-ok" : "stock-out"}>
                    {product.in_stock ? `可售 ${product.available_qty}` : "暂时缺货"}
                  </span>
                </div>
                <div className="card-actions">
                  <Link className="btn btn-ghost" href={`/product/${product.sku}`}>
                    查看详情
                  </Link>
                  <button
                    className="btn btn-primary"
                    type="button"
                    data-testid={`add-${product.sku}`}
                    disabled={!product.in_stock || adding === product.sku}
                    onClick={() => void addToCart(product)}
                  >
                    {adding === product.sku ? "加入中" : "加入购物车"}
                  </button>
                </div>
              </article>
            ))}
          </div>
        )}
      </section>
    </>
  );
}
