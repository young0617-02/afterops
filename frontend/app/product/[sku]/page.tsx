"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";

import { useCustomer } from "@/components/CustomerProvider";
import { api, errorMessage } from "@/lib/api";
import { formatDateTime } from "@/lib/status";
import type { ProductDetail, ProductReviews } from "@/lib/types";

export default function ProductPage() {
  const params = useParams<{ sku: string }>();
  const sku = Array.isArray(params?.sku) ? params.sku[0] : params?.sku ?? "";
  const { customer } = useCustomer();
  const [product, setProduct] = useState<ProductDetail | null>(null);
  const [reviews, setReviews] = useState<ProductReviews | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [adding, setAdding] = useState(false);

  useEffect(() => {
    if (!sku) return;
    let cancelled = false;
    setLoading(true);
    api<ProductDetail>(`/api/store/products/${encodeURIComponent(sku)}`)
      .then((data) => {
        if (!cancelled) {
          setProduct(data);
          setError("");
        }
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
  }, [sku]);

  useEffect(() => {
    if (!sku) return;
    let cancelled = false;
    api<ProductReviews>(
      `/api/store/products/${encodeURIComponent(sku)}/reviews`,
    )
      .then((data) => {
        if (!cancelled) setReviews(data);
      })
      .catch(() => {
        if (!cancelled) setReviews(null);
      });
    return () => {
      cancelled = true;
    };
  }, [sku]);

  const addToCart = async () => {
    if (!customer) {
      setNotice("请先在右上角用手机号登录");
      return;
    }
    if (!product) return;
    setAdding(true);
    setNotice("");
    try {
      await api("/api/store/cart/items", {
        method: "POST",
        body: JSON.stringify({ sku: product.sku, qty: 1 }),
      });
      setNotice("已加入购物车");
    } catch (err) {
      setNotice(errorMessage(err));
    } finally {
      setAdding(false);
    }
  };

  if (loading) return <div className="loading">加载商品中…</div>;
  if (!product) {
    return (
      <div className="empty-state">
        {error || "商品不存在"}
        <div style={{ marginTop: 12 }}>
          <Link className="btn btn-ghost" href="/">
            返回店面
          </Link>
        </div>
      </div>
    );
  }

  return (
    <>
      <div className="section-head">
        <div>
          <h2>{product.title}</h2>
          <p>
            {product.category} · {product.brand} · {product.sku}
            {product.variant_label
              ? ` · ${product.variant_label}`
              : ""}
          </p>
        </div>
        <Link className="btn btn-ghost" href="/">
          返回店面
        </Link>
      </div>

      {notice ? <div className="notice notice-warn">{notice}</div> : null}

      {product.variants.length > 1 ? (
        <div className="variant-selector" data-testid="product-variants">
          <span className="muted">规格</span>
          {product.variants.map((variant) => (
            <Link
              className={
                variant.sku === product.sku
                  ? "variant-option selected"
                  : "variant-option"
              }
              href={`/product/${encodeURIComponent(variant.sku)}`}
              key={variant.sku}
              data-testid={`variant-${variant.sku}`}
            >
              <strong>{variant.variant_label || variant.sku}</strong>
              <small>
                {variant.price_display} 元 ·{" "}
                {variant.in_stock
                  ? `可售 ${variant.available_qty}`
                  : "缺货"}
              </small>
            </Link>
          ))}
        </div>
      ) : null}

      <div className="product-hero">
        <div className="panel panel-pad">
          {product.image_url ? (
            <div className="product-image">
              <img src={product.image_url} alt={product.title} />
            </div>
          ) : (
            <div className="product-image">
              <span>{product.category || "商品"}</span>
            </div>
          )}
          {product.gallery_urls.length > 0 ? (
            <div className="product-gallery" data-testid="product-gallery">
              {product.gallery_urls.map((url) => (
                <a
                  key={url}
                  href={url}
                  target="_blank"
                  rel="noreferrer"
                >
                  <img
                    src={url}
                    alt={`${product.title} 详情图`}
                    loading="lazy"
                  />
                </a>
              ))}
            </div>
          ) : null}
          <div className="price-row">
            <span className="price">
              {product.price_display}
              <small>元</small>
            </span>
            <span className={product.in_stock ? "stock-ok" : "stock-out"}>
              {product.in_stock ? `当前可售 ${product.available_qty}` : "暂时缺货"}
            </span>
          </div>
          <p>{product.description}</p>
          <dl className="kv">
            <dt>规格</dt>
            <dd>{product.specs}</dd>
            <dt>无理由退货</dt>
            <dd>{product.support_7d_no_reason ? "支持" : "不支持"}</dd>
            <dt>保修期</dt>
            <dd>
              {product.warranty_months > 0
                ? `${product.warranty_months} 个月`
                : "不提供保修"}
            </dd>
            <dt>上架时间</dt>
            <dd>{product.listed_at}</dd>
            <dt>价格口径</dt>
            <dd>页面价，提交订单时以系统结算金额为准</dd>
          </dl>
          <div className="card-actions" style={{ marginTop: 18 }}>
            <button
              className="btn btn-primary"
              type="button"
              disabled={!product.in_stock || adding}
              onClick={() => void addToCart()}
            >
              {adding ? "加入中" : "加入购物车"}
            </button>
          </div>
        </div>

        <div className="panel">
          <div className="panel-head">
            <h3>分仓库存</h3>
            <span className="badge badge-neutral">查询时点快照</span>
          </div>
          <table className="table">
            <thead>
              <tr>
                <th>仓位</th>
                <th>仓库</th>
                <th>可售</th>
              </tr>
            </thead>
            <tbody>
              {product.inventory.map((row) => (
                <tr key={row.warehouse_id}>
                  <td>{row.warehouse_id}</td>
                  <td>{row.warehouse_name}</td>
                  <td>{row.qty_available}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <section
        className="panel panel-pad"
        style={{ marginTop: 16 }}
        data-testid="product-reviews"
      >
        <div className="section-head" style={{ marginBottom: 10 }}>
          <div>
            <h3 style={{ margin: 0 }}>商品评价</h3>
            <p>仅展示已签收订单顾客的评价；不展示联系方式。</p>
          </div>
          <span className="muted">
            {reviews?.count ?? 0} 条 · 平均{" "}
            {reviews?.average ?? "—"} 星
          </span>
        </div>
        {reviews && reviews.reviews.length > 0 ? (
          <div className="admin-grid">
            {reviews.reviews.map((review) => (
              <article
                className="proposal-card"
                key={review.id}
                data-testid={`product-review-${review.id}`}
              >
                <div className="proposal-head">
                  <div className="proposal-title">
                    <strong>
                      {"★".repeat(review.rating)}
                      {"☆".repeat(5 - review.rating)}
                    </strong>
                    <span className="badge badge-neutral">
                      {review.customer_name}
                    </span>
                  </div>
                  <span className="muted">
                    {formatDateTime(review.created_at)}
                  </span>
                </div>
                {review.content ? (
                  <p className="after-sales-reason">{review.content}</p>
                ) : null}
                {review.image_urls.length > 0 ? (
                  <div className="review-images">
                    {review.image_urls.map((url) => (
                      <a
                        href={url}
                        target="_blank"
                        rel="noreferrer"
                        key={url}
                      >
                        <img
                          src={url}
                          alt="评价图片"
                          className="review-image"
                          loading="lazy"
                        />
                      </a>
                    ))}
                  </div>
                ) : null}
                {review.follow_up_content ? (
                  <div className="review-follow-up">
                    <strong>追评</strong>
                    <p>{review.follow_up_content}</p>
                    {review.follow_up_image_urls.length > 0 ? (
                      <div className="review-images">
                        {review.follow_up_image_urls.map((url) => (
                          <a
                            href={url}
                            target="_blank"
                            rel="noreferrer"
                            key={url}
                          >
                            <img
                              src={url}
                              alt="追评图片"
                              className="review-image"
                              loading="lazy"
                            />
                          </a>
                        ))}
                      </div>
                    ) : null}
                  </div>
                ) : null}
                {review.merchant_reply ? (
                  <div className="merchant-reply">
                    <strong>商家回复</strong>
                    <p>{review.merchant_reply}</p>
                  </div>
                ) : null}
              </article>
            ))}
          </div>
        ) : (
          <div className="empty-state">还没有评价。</div>
        )}
      </section>
    </>
  );
}
