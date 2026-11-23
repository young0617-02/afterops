"use client";

import {
  Fragment,
  useCallback,
  useEffect,
  useRef,
  useState,
  type FormEvent,
} from "react";

import { StatusBadge } from "@/components/StatusBadge";
import { StockTransfersPanel } from "@/components/StockTransfersPanel";
import { api, errorMessage } from "@/lib/api";
import { formatDateTime, productStatusLabel } from "@/lib/status";
import type {
  AdminProduct,
  InventoryMovement,
  InventoryReconciliation,
  InventoryValuation,
} from "@/lib/types";

const MOVEMENT_REASON_LABELS: Record<string, string> = {
  opening_balance: "期初余额",
  product_created: "商品创建",
  admin_adjustment: "手工调整",
  stocktake: "库存盘点",
  transfer_out: "调拨调出",
  transfer_in: "调拨调入",
  transfer_cancel_in: "调拨取消回补",
  checkout_reserved: "结算预留",
  order_cancelled: "订单取消",
  order_expired: "订单超时",
  return_restocked: "退货回补",
  exchange_allocated: "换货占用",
};

const EMPTY_CREATE = {
  sku: "",
  title: "",
  category: "",
  brand: "",
  price: "",
  cost: "0",
  variant_group: "",
  variant_label: "",
  initial_qty: "0",
  warranty: "12",
  description: "",
  specs: "",
  support: true,
  status: "active",
};

type EditForm = {
  title: string;
  category: string;
  brand: string;
  description: string;
  specs: string;
  price: string;
  cost: string;
  variant_group: string;
  variant_label: string;
  warranty: string;
  support: boolean;
  status: string;
};

function priceToCents(value: string): number | null {
  const amount = Number(value);
  if (!Number.isFinite(amount) || amount < 0) return null;
  return Math.round(amount * 100);
}

function formatMoney(cents: number): string {
  return (cents / 100).toFixed(2);
}

function productTone(status: string): string {
  if (status === "active") return "badge-ok";
  if (status === "draft") return "badge-warn";
  return "badge-muted";
}

export function AdminProducts({ role }: { role: "owner" | "agent" }) {
  const [products, setProducts] = useState<AdminProduct[]>([]);
  const [searchInput, setSearchInput] = useState("");
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [createForm, setCreateForm] = useState({ ...EMPTY_CREATE });
  const [editingSku, setEditingSku] = useState<string | null>(null);
  const [editForm, setEditForm] = useState<EditForm | null>(null);
  const [inventoryEdits, setInventoryEdits] = useState<Record<string, string>>(
    {},
  );
  const [warehouseForm, setWarehouseForm] = useState({
    warehouse_id: "",
    warehouse_name: "",
    qty_available: "0",
  });
  const [transferSource, setTransferSource] = useState<string | null>(null);
  const [transferForm, setTransferForm] = useState({
    warehouse_id: "",
    quantity: "1",
    note: "",
  });
  const [movementSku, setMovementSku] = useState("");
  const [movements, setMovements] = useState<InventoryMovement[]>([]);
  const [movementBusy, setMovementBusy] = useState(false);
  const [movementError, setMovementError] = useState("");
  const [reconciliation, setReconciliation] =
    useState<InventoryReconciliation | null>(null);
  const [reconciliationBusy, setReconciliationBusy] = useState(false);
  const [reconciliationError, setReconciliationError] = useState("");
  const [valuation, setValuation] = useState<InventoryValuation | null>(null);
  const [valuationBusy, setValuationBusy] = useState(false);
  const [valuationError, setValuationError] = useState("");
  const [valuationSku, setValuationSku] = useState("");
  const valuationSeq = useRef(0);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams();
      if (search.trim()) params.set("q", search.trim());
      if (statusFilter) params.set("status", statusFilter);
      const suffix = params.toString() ? `?${params.toString()}` : "";
      setProducts(await api<AdminProduct[]>(`/api/admin/products${suffix}`));
      setError("");
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setLoading(false);
    }
  }, [search, statusFilter]);

  useEffect(() => {
    void load();
  }, [load]);

  const createProduct = async (event: FormEvent) => {
    event.preventDefault();
    const price = priceToCents(createForm.price);
    if (price === null) {
      setError("请输入有效的商品价格");
      return;
    }
    const cost = priceToCents(createForm.cost);
    if (cost === null) {
      setError("请输入有效的成本价");
      return;
    }
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const created = await api<AdminProduct>("/api/admin/products", {
        method: "POST",
        body: JSON.stringify({
          sku: createForm.sku.trim().toUpperCase(),
          title: createForm.title.trim(),
          category: createForm.category.trim(),
          brand: createForm.brand.trim(),
          description: createForm.description.trim(),
          specs: createForm.specs.trim(),
          price_cents: price,
          cost_cents: cost,
          variant_group: createForm.variant_group.trim(),
          variant_label: createForm.variant_label.trim(),
          support_7d_no_reason: createForm.support,
          warranty_months: Number(createForm.warranty),
          status: createForm.status,
          initial_qty: Number(createForm.initial_qty),
        }),
      });
      setNotice(`商品 ${created.sku} 已创建`);
      setCreateForm({ ...EMPTY_CREATE });
      await load();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  const importProducts = async (file: File | undefined) => {
    if (!file) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const data = new FormData();
      data.append("file", file);
      const result = await api<{ created: number; skus: string[] }>(
        "/api/admin/products/import",
        { method: "POST", body: data },
      );
      setNotice(`已导入 ${result.created} 个商品`);
      await load();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  const importInventory = async (file: File | undefined) => {
    if (!file) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const data = new FormData();
      data.append("file", file);
      const result = await api<{
        updated: number;
        changed: number;
        skus: string[];
      }>("/api/admin/inventory/import", {
        method: "POST",
        body: data,
      });
      setNotice(
        `已处理 ${result.updated} 行库存（变更 ${result.changed} 行）`,
      );
      await load();
      await loadMovements();
      await loadReconciliation();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  const startEdit = (product: AdminProduct) => {
    setEditingSku(product.sku);
    setEditForm({
      title: product.title,
      category: product.category,
      brand: product.brand,
      description: product.description,
      specs: product.specs,
      price: (product.price_cents / 100).toFixed(2),
      cost: ((product.cost_cents ?? 0) / 100).toFixed(2),
      variant_group: product.variant_group,
      variant_label: product.variant_label,
      warranty: String(product.warranty_months),
      support: product.support_7d_no_reason,
      status: product.status,
    });
    setWarehouseForm({
      warehouse_id: "",
      warehouse_name: "",
      qty_available: "0",
    });
  };

  const saveProduct = async (product: AdminProduct) => {
    if (!editForm) return;
    const price = priceToCents(editForm.price);
    if (price === null) {
      setError("请输入有效的商品价格");
      return;
    }
    const cost = priceToCents(editForm.cost);
    if (cost === null) {
      setError("请输入有效的成本价");
      return;
    }
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await api<AdminProduct>(`/api/admin/products/${product.sku}`, {
        method: "PATCH",
        body: JSON.stringify({
          title: editForm.title.trim(),
          category: editForm.category.trim(),
          brand: editForm.brand.trim(),
          description: editForm.description.trim(),
          specs: editForm.specs.trim(),
          price_cents: price,
          cost_cents: cost,
          variant_group: editForm.variant_group.trim(),
          variant_label: editForm.variant_label.trim(),
          support_7d_no_reason: editForm.support,
          warranty_months: Number(editForm.warranty),
          status: editForm.status,
        }),
      });
      setNotice(`商品 ${product.sku} 已更新`);
      setEditingSku(null);
      setEditForm(null);
      await load();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  const uploadImage = async (sku: string, file: File | undefined) => {
    if (!file) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const form = new FormData();
      form.append("file", file);
      await api<AdminProduct>(`/api/admin/products/${sku}/image`, {
        method: "POST",
        body: form,
      });
      setNotice(`商品 ${sku} 主图已更新`);
      await load();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  const uploadGallery = async (sku: string, files: File[]) => {
    if (files.length === 0) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      for (const file of files) {
        const form = new FormData();
        form.append("file", file);
        await api<AdminProduct>(`/api/admin/products/${sku}/images`, {
          method: "POST",
          body: form,
        });
      }
      setNotice(`商品 ${sku} 详情图已更新`);
      await load();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  const removeGalleryImage = async (sku: string, storageName: string) => {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await api<AdminProduct>(
        `/api/admin/products/${sku}/images/${encodeURIComponent(storageName)}`,
        { method: "DELETE" },
      );
      setNotice(`商品 ${sku} 详情图已删除`);
      await load();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  const removeImage = async (sku: string) => {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await api<AdminProduct>(`/api/admin/products/${sku}/image`, {
        method: "DELETE",
      });
      setNotice(`商品 ${sku} 主图已移除`);
      await load();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  const stocktakeInventory = async (
    product: AdminProduct,
    warehouseId: string,
  ) => {
    const key = `${product.sku}:${warehouseId}`;
    const row = product.inventory.find(
      (item) => item.warehouse_id === warehouseId,
    );
    const qty = Number(inventoryEdits[key] ?? String(row?.qty_available ?? 0));
    if (!Number.isInteger(qty) || qty < 0) {
      setError("盘点数量必须是非负整数");
      return;
    }
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const result = await api<{
        before_qty: number;
        after_qty: number;
        delta: number;
      }>(
        `/api/admin/products/${product.sku}/inventory/${warehouseId}/stocktake`,
        {
          method: "POST",
          body: JSON.stringify({
            counted_qty: qty,
            note: "后台盘点",
          }),
        },
      );
      setNotice(
        `${product.sku} / ${warehouseId} 盘点完成：${result.before_qty} → ${result.after_qty}`,
      );
      setInventoryEdits((previous) => {
        const next = { ...previous };
        delete next[key];
        return next;
      });
      await load();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  const startTransfer = (product: AdminProduct, warehouseId: string) => {
    setTransferSource(`${product.sku}:${warehouseId}`);
    setTransferForm({ warehouse_id: "", quantity: "1", note: "" });
    setError("");
    setNotice("");
  };

  const submitTransfer = async (
    product: AdminProduct,
    fromWarehouseId: string,
  ) => {
    const target = transferForm.warehouse_id.trim();
    const quantity = Number(transferForm.quantity);
    if (!target) {
      setError("请输入调入仓库编号");
      return;
    }
    if (!Number.isInteger(quantity) || quantity <= 0) {
      setError("调拨数量必须是正整数");
      return;
    }
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const result = await api<{
        from_warehouse_id: string;
        to_warehouse_id: string;
        quantity: number;
      }>(`/api/admin/products/${product.sku}/inventory/transfer`, {
        method: "POST",
        body: JSON.stringify({
          from_warehouse_id: fromWarehouseId,
          to_warehouse_id: target,
          quantity,
          note: transferForm.note.trim(),
        }),
      });
      setNotice(
        `${product.sku}：${result.from_warehouse_id} → ${result.to_warehouse_id} 调拨 ${result.quantity} 件完成`,
      );
      setTransferSource(null);
      await load();
      await loadMovements();
      await loadReconciliation();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  const saveInventory = async (
    product: AdminProduct,
    warehouseId: string,
  ) => {
    const key = `${product.sku}:${warehouseId}`;
    const row = product.inventory.find(
      (item) => item.warehouse_id === warehouseId,
    );
    const qty = Number(inventoryEdits[key] ?? String(row?.qty_available ?? 0));
    if (!Number.isInteger(qty) || qty < 0) {
      setError("库存必须是非负整数");
      return;
    }
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await api<AdminProduct>(
        `/api/admin/products/${product.sku}/inventory/${warehouseId}`,
        {
          method: "PUT",
          body: JSON.stringify({ qty_available: qty }),
        },
      );
      setNotice(`${product.sku} / ${warehouseId} 库存已更新为 ${qty}`);
      setInventoryEdits((previous) => {
        const next = { ...previous };
        delete next[key];
        return next;
      });
      await load();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  const addWarehouse = async (product: AdminProduct) => {
    const qty = Number(warehouseForm.qty_available);
    if (!warehouseForm.warehouse_id.trim() || !Number.isInteger(qty) || qty < 0) {
      setError("请填写仓库编号和非负整数库存");
      return;
    }
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await api<AdminProduct>(
        `/api/admin/products/${product.sku}/inventory/${warehouseForm.warehouse_id.trim()}`,
        {
          method: "PUT",
          body: JSON.stringify({
            warehouse_name: warehouseForm.warehouse_name.trim(),
            qty_available: qty,
          }),
        },
      );
      setNotice(`${product.sku} 已更新仓库 ${warehouseForm.warehouse_id.trim()}`);
      setWarehouseForm({
        warehouse_id: "",
        warehouse_name: "",
        qty_available: "0",
      });
      await load();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  const loadReconciliation = async () => {
    setReconciliationBusy(true);
    setReconciliationError("");
    try {
      setReconciliation(
        await api<InventoryReconciliation>(
          "/api/admin/inventory-reconciliation",
        ),
      );
    } catch (err) {
      setReconciliationError(errorMessage(err));
    } finally {
      setReconciliationBusy(false);
    }
  };

  useEffect(() => {
    void loadReconciliation();
  }, []);

  const loadValuation = useCallback(async () => {
    const seq = ++valuationSeq.current;
    setValuationBusy(true);
    setValuationError("");
    try {
      const params = new URLSearchParams();
      if (valuationSku.trim()) {
        params.set("sku", valuationSku.trim().toUpperCase());
      }
      const suffix = params.toString() ? `?${params.toString()}` : "";
      const result = await api<InventoryValuation>(
        `/api/admin/inventory-valuation${suffix}`,
      );
      if (seq === valuationSeq.current) setValuation(result);
    } catch (err) {
      if (seq === valuationSeq.current) {
        setValuationError(errorMessage(err));
      }
    } finally {
      if (seq === valuationSeq.current) setValuationBusy(false);
    }
  }, [valuationSku]);

  useEffect(() => {
    if (role === "owner") void loadValuation();
  }, [role, products, loadValuation]);

  const loadMovements = async () => {
    setMovementBusy(true);
    setMovementError("");
    try {
      const params = new URLSearchParams({ limit: "50" });
      if (movementSku.trim()) {
        params.set("sku", movementSku.trim().toUpperCase());
      }
      setMovements(
        await api<InventoryMovement[]>(
          `/api/admin/inventory-movements?${params.toString()}`,
        ),
      );
    } catch (err) {
      setMovementError(errorMessage(err));
    } finally {
      setMovementBusy(false);
    }
  };

  return (
    <section className="admin-products">
      <div className="fulfillment-toolbar">
        <form
          className="search-form product-search"
          onSubmit={(event) => {
            event.preventDefault();
            setSearch(searchInput);
          }}
        >
          <input
            value={searchInput}
            onChange={(event) => setSearchInput(event.target.value)}
            placeholder="搜索 SKU、标题、品牌、类目…"
            maxLength={80}
            aria-label="搜索商品"
          />
          <button className="btn btn-ghost btn-sm" type="submit">
            搜索
          </button>
          <select
            value={statusFilter}
            onChange={(event) => setStatusFilter(event.target.value)}
            aria-label="按商品状态筛选"
          >
            <option value="">全部状态</option>
            <option value="active">在售</option>
            <option value="draft">草稿</option>
            <option value="archived">已下架</option>
          </select>
          <button
            className="btn btn-ghost btn-sm"
            type="button"
            onClick={() => void load()}
          >
            刷新
          </button>
          <a
            className="btn btn-ghost btn-sm"
            href="/api/admin/inventory/export.csv"
            download
            data-testid="inventory-export"
          >
            导出库存
          </a>
        </form>
      </div>

      <StockTransfersPanel />

      <section
        className="panel panel-pad"
        data-testid="inventory-movements"
        style={{ marginBottom: 16 }}
      >
        <div className="section-head" style={{ marginBottom: 10 }}>
          <div>
            <h3 style={{ margin: 0 }}>库存流水</h3>
            <p>
              结算预留、订单释放、退货回补、换货占用与手工调整都会记录前后数量。
            </p>
          </div>
          <form
            className="search-form"
            onSubmit={(event) => {
              event.preventDefault();
              void loadMovements();
            }}
          >
            <input
              value={movementSku}
              onChange={(event) =>
                setMovementSku(event.target.value.toUpperCase())
              }
              placeholder="按 SKU 查询，留空看最近"
              maxLength={64}
              aria-label="按 SKU 查询库存流水"
              data-testid="movement-sku"
            />
            <button
              className="btn btn-ghost btn-sm"
              type="submit"
              data-testid="load-movements"
              disabled={movementBusy}
            >
              {movementBusy ? "查询中" : "查询"}
            </button>
            <a
              className="btn btn-ghost btn-sm"
              href={`/api/admin/inventory-movements/export.csv${
                movementSku.trim()
                  ? `?sku=${encodeURIComponent(movementSku.trim())}`
                  : ""
              }`}
              download
              data-testid="movement-export"
            >
              导出流水
            </a>
          </form>
        </div>

        {movementError ? (
          <div className="notice notice-error">{movementError}</div>
        ) : null}
        {movements.length === 0 ? (
          <div className="muted">
            输入 SKU 查询；留空查询最近 50 条变动。
          </div>
        ) : (
          <table className="table" data-testid="movement-table">
            <thead>
              <tr>
                <th>时间</th>
                <th>商品</th>
                <th>仓库</th>
                <th>变动</th>
                <th>结存</th>
                <th>原因</th>
                <th>关联</th>
                <th>操作人</th>
              </tr>
            </thead>
            <tbody>
              {movements.map((row) => (
                <tr
                  key={row.id}
                  data-testid={`movement-${row.id}`}
                  title={row.note || undefined}
                >
                  <td>{formatDateTime(row.created_at)}</td>
                  <td>
                    {row.product_title}
                    <small className="muted"> · {row.sku}</small>
                  </td>
                  <td>
                    {row.warehouse_name || row.warehouse_id}
                  </td>
                  <td>
                    <span
                      className={
                        row.qty_delta > 0
                          ? "badge badge-ok"
                          : "badge badge-warn"
                      }
                    >
                      {row.qty_delta > 0 ? "+" : ""}
                      {row.qty_delta}
                    </span>
                  </td>
                  <td>{row.qty_after}</td>
                  <td>
                    {MOVEMENT_REASON_LABELS[row.reason] ?? row.reason}
                  </td>
                  <td>
                    {row.order_no ??
                      (row.return_id ? `退货 #${row.return_id}` : "—")}
                  </td>
                  <td>{row.operator_email ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      {role === "owner" ? (
        <section
          className="panel panel-pad"
          data-testid="inventory-valuation"
          style={{ marginBottom: 16 }}
        >
          <div className="section-head" style={{ marginBottom: 10 }}>
            <div>
              <h3 style={{ margin: 0 }}>库存成本估值</h3>
              <p>
                按商品成本价 × 在库数量（可售 + 预留）；成本价取当前商品设置。
              </p>
            </div>
            <div className="search-form">
              <input
                value={valuationSku}
                onChange={(event) => setValuationSku(event.target.value)}
                placeholder="按 SKU 过滤"
                maxLength={64}
                data-testid="valuation-sku"
              />
              <button
                className="btn btn-ghost btn-sm"
                type="button"
                disabled={valuationBusy}
                onClick={() => void loadValuation()}
                data-testid="refresh-valuation"
              >
                {valuationBusy ? "计算中" : "刷新估值"}
              </button>
            </div>
          </div>
          {valuationError ? (
            <div className="notice notice-error">{valuationError}</div>
          ) : null}
          {valuation ? (
            <>
              <div className="audit-summary">
                <span>在库件数 {valuation.total_qty}</span>
                <span data-testid="inventory-value-total">
                  成本总值 {formatMoney(valuation.total_value_cents)} 元
                </span>
              </div>
              {valuation.lines.length === 0 ? (
                <div className="muted">没有在库商品。</div>
              ) : (
                <table className="table">
                  <thead>
                    <tr>
                      <th>商品</th>
                      <th>仓库</th>
                      <th>在库</th>
                      <th>成本价</th>
                      <th>估值</th>
                    </tr>
                  </thead>
                  <tbody>
                    {valuation.lines.slice(0, 20).map((line) => (
                      <tr
                        key={`${line.sku}-${line.warehouse_id}`}
                        data-testid={`valuation-${line.sku}-${line.warehouse_id}`}
                      >
                        <td>
                          {line.title}
                          <small className="muted"> · {line.sku}</small>
                        </td>
                        <td>{line.warehouse_name || line.warehouse_id}</td>
                        <td>{line.qty_on_hand}</td>
                        <td>{formatMoney(line.cost_cents)} 元</td>
                        <td>{formatMoney(line.value_cents)} 元</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </>
          ) : null}
        </section>
      ) : null}

      <section
        className="panel panel-pad"
        data-testid="inventory-reconciliation"
        style={{ marginBottom: 16 }}
      >
        <div className="section-head" style={{ marginBottom: 10 }}>
          <div>
            <h3 style={{ margin: 0 }}>库存对账</h3>
            <p>
              流水累计应与当前可售库存一致；未跟踪表示该库存行还没有任何流水。
            </p>
          </div>
          <button
            className="btn btn-ghost btn-sm"
            type="button"
            disabled={reconciliationBusy}
            onClick={() => void loadReconciliation()}
          >
            {reconciliationBusy ? "对账中" : "重新对账"}
          </button>
        </div>
        {reconciliationError ? (
          <div className="notice notice-error">{reconciliationError}</div>
        ) : null}
        {reconciliation ? (
          <>
            <div className="audit-summary">
              <span>已跟踪 {reconciliation.tracked}</span>
              <span>账实一致 {reconciliation.matched}</span>
              <span>未跟踪 {reconciliation.untracked}</span>
              <span
                className={
                  reconciliation.discrepancies.length > 0
                    ? "badge badge-danger"
                    : "badge badge-ok"
                }
              >
                差异 {reconciliation.discrepancies.length}
              </span>
            </div>
            {reconciliation.discrepancies.length > 0 ? (
              <table className="table">
                <thead>
                  <tr>
                    <th>商品</th>
                    <th>仓库</th>
                    <th>当前可售</th>
                    <th>流水累计</th>
                    <th>差异</th>
                  </tr>
                </thead>
                <tbody>
                  {reconciliation.discrepancies.map((row) => (
                    <tr
                      key={`${row.product_id}-${row.warehouse_id}`}
                      data-testid={`reconciliation-${row.sku}-${row.warehouse_id}`}
                    >
                      <td>
                        {row.product_title}
                        <small className="muted"> · {row.sku}</small>
                      </td>
                      <td>{row.warehouse_name || row.warehouse_id}</td>
                      <td>{row.qty_available}</td>
                      <td>{row.movement_total}</td>
                      <td>
                        <span className="badge badge-danger">
                          {row.delta > 0 ? "+" : ""}
                          {row.delta}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              <div className="muted">已跟踪库存行全部账实一致。</div>
            )}
          </>
        ) : null}
      </section>

      {role !== "owner" ? (
        <div className="notice notice-warn">
          客服账号只能查看商品与库存；新建、改价、上下架和库存调整需要店主权限。
        </div>
      ) : null}
      {notice ? <div className="notice notice-success">{notice}</div> : null}
      {error ? <div className="notice notice-error">{error}</div> : null}

      {role === "owner" ? (
        <>
        <details className="product-create">
          <summary>新增商品</summary>
          <form className="product-edit-form" onSubmit={createProduct}>
            <div className="field">
              <label htmlFor="new-sku">SKU</label>
              <input
                id="new-sku"
                data-testid="new-sku"
                value={createForm.sku}
                onChange={(event) =>
                  setCreateForm({
                    ...createForm,
                    sku: event.target.value.toUpperCase(),
                  })
                }
                maxLength={64}
                required
              />
            </div>
            <div className="field">
              <label htmlFor="new-title">标题</label>
              <input
                id="new-title"
                data-testid="new-title"
                value={createForm.title}
                onChange={(event) =>
                  setCreateForm({ ...createForm, title: event.target.value })
                }
                maxLength={255}
                required
              />
            </div>
            <div className="field">
              <label htmlFor="new-category">类目</label>
              <input
                id="new-category"
                value={createForm.category}
                onChange={(event) =>
                  setCreateForm({ ...createForm, category: event.target.value })
                }
                maxLength={80}
              />
            </div>
            <div className="field">
              <label htmlFor="new-brand">品牌</label>
              <input
                id="new-brand"
                value={createForm.brand}
                onChange={(event) =>
                  setCreateForm({ ...createForm, brand: event.target.value })
                }
                maxLength={80}
              />
            </div>
            <div className="field">
              <label htmlFor="new-price">价格（元）</label>
              <input
                id="new-price"
                data-testid="new-price"
                type="number"
                min="0"
                step="0.01"
                value={createForm.price}
                onChange={(event) =>
                  setCreateForm({ ...createForm, price: event.target.value })
                }
                required
              />
            </div>
            <div className="field">
              <label htmlFor="new-cost">成本价（元）</label>
              <input
                id="new-cost"
                data-testid="new-cost"
                type="number"
                min="0"
                step="0.01"
                value={createForm.cost}
                onChange={(event) =>
                  setCreateForm({ ...createForm, cost: event.target.value })
                }
              />
            </div>
            <div className="field">
              <label htmlFor="new-variant-group">规格组</label>
              <input
                id="new-variant-group"
                data-testid="new-variant-group"
                value={createForm.variant_group}
                onChange={(event) =>
                  setCreateForm({
                    ...createForm,
                    variant_group: event.target.value,
                  })
                }
                maxLength={64}
                placeholder="如 NCM-001"
              />
            </div>
            <div className="field">
              <label htmlFor="new-variant-label">规格名称</label>
              <input
                id="new-variant-label"
                data-testid="new-variant-label"
                value={createForm.variant_label}
                onChange={(event) =>
                  setCreateForm({
                    ...createForm,
                    variant_label: event.target.value,
                  })
                }
                maxLength={80}
                placeholder="如 曜石黑"
              />
            </div>
            <div className="field">
              <label htmlFor="new-warranty">保修期（月）</label>
              <input
                id="new-warranty"
                data-testid="new-warranty"
                type="number"
                min="0"
                max="120"
                step="1"
                value={createForm.warranty}
                onChange={(event) =>
                  setCreateForm({ ...createForm, warranty: event.target.value })
                }
              />
            </div>
            <div className="field">
              <label htmlFor="new-stock">初始库存</label>
              <input
                id="new-stock"
                data-testid="new-stock"
                type="number"
                min="0"
                step="1"
                value={createForm.initial_qty}
                onChange={(event) =>
                  setCreateForm({
                    ...createForm,
                    initial_qty: event.target.value,
                  })
                }
              />
            </div>
            <div className="field">
              <label htmlFor="new-status">状态</label>
              <select
                id="new-status"
                value={createForm.status}
                onChange={(event) =>
                  setCreateForm({ ...createForm, status: event.target.value })
                }
              >
                <option value="active">在售</option>
                <option value="draft">草稿</option>
                <option value="archived">已下架</option>
              </select>
            </div>
            <div className="field product-full">
              <label htmlFor="new-description">描述</label>
              <textarea
                id="new-description"
                value={createForm.description}
                onChange={(event) =>
                  setCreateForm({
                    ...createForm,
                    description: event.target.value,
                  })
                }
                maxLength={5000}
                rows={2}
              />
            </div>
            <div className="field product-full">
              <label htmlFor="new-specs">参数</label>
              <textarea
                id="new-specs"
                value={createForm.specs}
                onChange={(event) =>
                  setCreateForm({ ...createForm, specs: event.target.value })
                }
                maxLength={5000}
                rows={2}
              />
            </div>
            <label className="product-check">
              <input
                type="checkbox"
                checked={createForm.support}
                onChange={(event) =>
                  setCreateForm({ ...createForm, support: event.target.checked })
                }
              />
              支持 7 天无理由
            </label>
            <button
              className="btn btn-primary"
              type="submit"
              data-testid="create-product"
              disabled={busy}
            >
              创建商品
            </button>
          </form>
        </details>
        <details className="product-create">
          <summary>批量导入 CSV</summary>
          <div className="product-edit-form">
            <p className="muted product-full">
              表头：sku,title,price 必填；可选 category,brand,cost,
              variant_group,variant_label,initial_qty,warranty_months,status,
              support_7d_no_reason。价格与成本单位为元，
              单次最多 200 行，整批校验通过才会创建。
            </p>
            <label
              className="btn btn-ghost btn-sm"
              style={{ cursor: "pointer" }}
            >
              选择 CSV 文件
              <input
                type="file"
                accept=".csv,text/csv"
                hidden
                data-testid="product-import-file"
                onChange={(event) => {
                  const file = event.target.files?.[0];
                  event.target.value = "";
                  void importProducts(file);
                }}
              />
            </label>
          </div>
        </details>
        <details className="product-create">
          <summary>批量导入库存 CSV</summary>
          <div className="product-edit-form">
            <p className="muted product-full">
              表头：sku,warehouse_id,qty_available 必填；可选 warehouse_name。
              数量为覆盖值，单次最多 500 行，整批校验通过才会写入并记录库存流水。
            </p>
            <label
              className="btn btn-ghost btn-sm"
              style={{ cursor: "pointer" }}
            >
              选择库存 CSV
              <input
                type="file"
                accept=".csv,text/csv"
                hidden
                data-testid="inventory-import-file"
                onChange={(event) => {
                  const file = event.target.files?.[0];
                  event.target.value = "";
                  void importInventory(file);
                }}
              />
            </label>
          </div>
        </details>
        </>
      ) : null}

      {loading ? (
        <div className="loading">加载商品…</div>
      ) : products.length === 0 ? (
        <div className="empty-state">当前筛选下没有商品。</div>
      ) : (
        <div className="admin-grid">
          {products.map((product) => (
            <article
              className="proposal-card"
              key={product.sku}
              data-testid={`admin-product-${product.sku}`}
            >
              <div className="proposal-head">
                <div className="proposal-title">
                  <strong>{product.title}</strong>
                  <span className="audit-type">{product.sku}</span>
                  <StatusBadge
                    label={productStatusLabel(product.status)}
                    tone={productTone(product.status)}
                  />
                </div>
                <span className="muted">
                  {formatDateTime(product.listed_at)}
                </span>
              </div>
              <div className="product-image-admin">
                {product.image_url ? (
                  <img src={product.image_url} alt={product.title} />
                ) : (
                  <span>暂无主图</span>
                )}
              </div>
              {product.gallery.length > 0 ? (
                <div className="admin-gallery">
                  {product.gallery.map((image) => (
                    <div className="admin-gallery-item" key={image.storage_name}>
                      <img src={image.url} alt={`${product.title} 详情图`} />
                      {role === "owner" ? (
                        <button
                          type="button"
                          aria-label="删除详情图"
                          disabled={busy}
                          data-testid={`remove-gallery-${product.sku}-${image.storage_name}`}
                          onClick={() =>
                            void removeGalleryImage(
                              product.sku,
                              image.storage_name,
                            )
                          }
                        >
                          ×
                        </button>
                      ) : null}
                    </div>
                  ))}
                </div>
              ) : null}
              {role === "owner" ? (
                <div className="proposal-actions">
                  <label
                    className="btn btn-ghost btn-sm"
                    style={{ cursor: "pointer" }}
                  >
                    上传主图
                    <input
                      type="file"
                      accept="image/png,image/jpeg,image/webp"
                      hidden
                      data-testid={`product-image-${product.sku}`}
                      onChange={(event) => {
                        const file = event.target.files?.[0];
                        event.target.value = "";
                        void uploadImage(product.sku, file);
                      }}
                    />
                  </label>
                  {product.image_url ? (
                    <button
                      className="btn btn-ghost btn-sm"
                      type="button"
                      disabled={busy}
                      data-testid={`remove-product-image-${product.sku}`}
                      onClick={() => void removeImage(product.sku)}
                    >
                      移除主图
                    </button>
                  ) : null}
                  <label
                    className="btn btn-ghost btn-sm"
                    style={{ cursor: "pointer" }}
                  >
                    上传详情图
                    <input
                      type="file"
                      accept="image/png,image/jpeg,image/webp"
                      multiple
                      hidden
                      data-testid={`product-gallery-${product.sku}`}
                      onChange={(event) => {
                        const files = Array.from(event.target.files ?? []);
                        event.target.value = "";
                        void uploadGallery(product.sku, files);
                      }}
                    />
                  </label>
                </div>
              ) : null}
              <div className="proposal-meta">
                <span>类目 {product.category || "—"}</span>
                <span>品牌 {product.brand || "—"}</span>
                <span>价格 {product.price_display} 元</span>
                {product.variant_label ? (
                  <span>
                    规格 {product.variant_label}
                    {product.variant_group
                      ? ` · ${product.variant_group}`
                      : ""}
                  </span>
                ) : null}
                {role === "owner" && product.cost_display !== null ? (
                  <span>成本 {product.cost_display} 元</span>
                ) : null}
                <span>保修 {product.warranty_months} 个月</span>
                <span>可售 {product.available_qty}</span>
                <span>预留 {product.reserved_qty}</span>
                <span>{product.support_7d_no_reason ? "7 天无理由" : "不支持 7 天无理由"}</span>
              </div>

              {role === "owner" ? (
                <div className="proposal-actions">
                  <button
                    className="btn btn-ghost btn-sm"
                    type="button"
                    data-testid={`edit-product-${product.sku}`}
                    onClick={() => startEdit(product)}
                  >
                    {editingSku === product.sku ? "编辑中" : "编辑商品"}
                  </button>
                </div>
              ) : null}

              {editingSku === product.sku && editForm ? (
                <div className="product-edit-form">
                  <div className="field">
                    <label>标题</label>
                    <input
                      data-testid={`edit-title-${product.sku}`}
                      value={editForm.title}
                      onChange={(event) =>
                        setEditForm({ ...editForm, title: event.target.value })
                      }
                      maxLength={255}
                    />
                  </div>
                  <div className="field">
                    <label>类目</label>
                    <input
                      value={editForm.category}
                      onChange={(event) =>
                        setEditForm({ ...editForm, category: event.target.value })
                      }
                      maxLength={80}
                    />
                  </div>
                  <div className="field">
                    <label>品牌</label>
                    <input
                      value={editForm.brand}
                      onChange={(event) =>
                        setEditForm({ ...editForm, brand: event.target.value })
                      }
                      maxLength={80}
                    />
                  </div>
                  <div className="field">
                    <label>价格（元）</label>
                    <input
                      data-testid={`edit-price-${product.sku}`}
                      type="number"
                      min="0"
                      step="0.01"
                      value={editForm.price}
                      onChange={(event) =>
                        setEditForm({ ...editForm, price: event.target.value })
                      }
                    />
                  </div>
                  <div className="field">
                    <label>成本价（元）</label>
                    <input
                      data-testid={`edit-cost-${product.sku}`}
                      type="number"
                      min="0"
                      step="0.01"
                      value={editForm.cost}
                      onChange={(event) =>
                        setEditForm({ ...editForm, cost: event.target.value })
                      }
                    />
                  </div>
                  <div className="field">
                    <label>规格组</label>
                    <input
                      data-testid={`edit-variant-group-${product.sku}`}
                      value={editForm.variant_group}
                      onChange={(event) =>
                        setEditForm({
                          ...editForm,
                          variant_group: event.target.value,
                        })
                      }
                      maxLength={64}
                    />
                  </div>
                  <div className="field">
                    <label>规格名称</label>
                    <input
                      data-testid={`edit-variant-label-${product.sku}`}
                      value={editForm.variant_label}
                      onChange={(event) =>
                        setEditForm({
                          ...editForm,
                          variant_label: event.target.value,
                        })
                      }
                      maxLength={80}
                    />
                  </div>
                  <div className="field">
                    <label>保修期（月）</label>
                    <input
                      data-testid={`edit-warranty-${product.sku}`}
                      type="number"
                      min="0"
                      max="120"
                      step="1"
                      value={editForm.warranty}
                      onChange={(event) =>
                        setEditForm({
                          ...editForm,
                          warranty: event.target.value,
                        })
                      }
                    />
                  </div>
                  <div className="field">
                    <label>状态</label>
                    <select
                      value={editForm.status}
                      onChange={(event) =>
                        setEditForm({ ...editForm, status: event.target.value })
                      }
                    >
                      <option value="active">在售</option>
                      <option value="draft">草稿</option>
                      <option value="archived">已下架</option>
                    </select>
                  </div>
                  <div className="field product-full">
                    <label>描述</label>
                    <textarea
                      value={editForm.description}
                      onChange={(event) =>
                        setEditForm({
                          ...editForm,
                          description: event.target.value,
                        })
                      }
                      maxLength={5000}
                      rows={2}
                    />
                  </div>
                  <div className="field product-full">
                    <label>参数</label>
                    <textarea
                      value={editForm.specs}
                      onChange={(event) =>
                        setEditForm({ ...editForm, specs: event.target.value })
                      }
                      maxLength={5000}
                      rows={2}
                    />
                  </div>
                  <label className="product-check">
                    <input
                      type="checkbox"
                      checked={editForm.support}
                      onChange={(event) =>
                        setEditForm({
                          ...editForm,
                          support: event.target.checked,
                        })
                      }
                    />
                    支持 7 天无理由
                  </label>
                  <button
                    className="btn btn-primary"
                    type="button"
                    data-testid={`save-product-${product.sku}`}
                    disabled={busy}
                    onClick={() => void saveProduct(product)}
                  >
                    保存商品
                  </button>
                </div>
              ) : null}

              <div className="inventory-list">
                <h4>库存</h4>
                {product.inventory.map((row) => {
                  const key = `${product.sku}:${row.warehouse_id}`;
                  return (
                    <Fragment key={key}>
                    <div className="inventory-row">
                      <span>
                        {row.warehouse_name}（{row.warehouse_id}）
                      </span>
                      <span>预留 {row.qty_reserved}</span>
                      {role === "owner" ? (
                        <>
                          <input
                            type="number"
                            min="0"
                            step="1"
                            aria-label={`${product.sku} ${row.warehouse_id} 可售库存`}
                            data-testid={`inventory-input-${product.sku}-${row.warehouse_id}`}
                            value={
                              inventoryEdits[key] ?? String(row.qty_available)
                            }
                            onChange={(event) =>
                              setInventoryEdits((previous) => ({
                                ...previous,
                                [key]: event.target.value,
                              }))
                            }
                          />
                          <button
                            className="btn btn-ghost btn-sm"
                            type="button"
                            data-testid={`save-inventory-${product.sku}-${row.warehouse_id}`}
                            disabled={busy}
                            onClick={() =>
                              void saveInventory(product, row.warehouse_id)
                            }
                          >
                            保存库存
                          </button>
                          <button
                            className="btn btn-ghost btn-sm"
                            type="button"
                            data-testid={`stocktake-inventory-${product.sku}-${row.warehouse_id}`}
                            disabled={busy}
                            onClick={() =>
                              void stocktakeInventory(
                                product,
                                row.warehouse_id,
                              )
                            }
                          >
                            盘点
                          </button>
                          <button
                            className="btn btn-ghost btn-sm"
                            type="button"
                            data-testid={`transfer-inventory-${product.sku}-${row.warehouse_id}`}
                            disabled={busy}
                            onClick={() =>
                              startTransfer(product, row.warehouse_id)
                            }
                          >
                            调拨
                          </button>
                        </>
                      ) : (
                        <span>可售 {row.qty_available}</span>
                      )}
                    </div>
                    {role === "owner" &&
                    transferSource === key ? (
                      <div
                        className="inventory-transfer"
                        data-testid={`transfer-form-${product.sku}-${row.warehouse_id}`}
                      >
                        <input
                          placeholder="调入仓库编号"
                          aria-label="调入仓库编号"
                          data-testid={`transfer-target-${product.sku}-${row.warehouse_id}`}
                          value={transferForm.warehouse_id}
                          onChange={(event) =>
                            setTransferForm({
                              ...transferForm,
                              warehouse_id: event.target.value,
                            })
                          }
                          maxLength={64}
                        />
                        <input
                          type="number"
                          min="1"
                          step="1"
                          aria-label="调拨数量"
                          data-testid={`transfer-qty-${product.sku}-${row.warehouse_id}`}
                          value={transferForm.quantity}
                          onChange={(event) =>
                            setTransferForm({
                              ...transferForm,
                              quantity: event.target.value,
                            })
                          }
                        />
                        <input
                          placeholder="备注（可选）"
                          aria-label="调拨备注"
                          data-testid={`transfer-note-${product.sku}-${row.warehouse_id}`}
                          value={transferForm.note}
                          onChange={(event) =>
                            setTransferForm({
                              ...transferForm,
                              note: event.target.value,
                            })
                          }
                          maxLength={255}
                        />
                        <button
                          className="btn btn-primary btn-sm"
                          type="button"
                          disabled={busy}
                          data-testid={`transfer-submit-${product.sku}-${row.warehouse_id}`}
                          onClick={() =>
                            void submitTransfer(product, row.warehouse_id)
                          }
                        >
                          确认调拨
                        </button>
                        <button
                          className="btn btn-ghost btn-sm"
                          type="button"
                          disabled={busy}
                          data-testid={`transfer-cancel-${product.sku}-${row.warehouse_id}`}
                          onClick={() => setTransferSource(null)}
                        >
                          取消
                        </button>
                      </div>
                    ) : null}
                    </Fragment>
                  );
                })}

                {role === "owner" && editingSku === product.sku ? (
                  <div className="inventory-new">
                    <input
                      placeholder="仓库编号"
                      value={warehouseForm.warehouse_id}
                      onChange={(event) =>
                        setWarehouseForm({
                          ...warehouseForm,
                          warehouse_id: event.target.value,
                        })
                      }
                      maxLength={64}
                      aria-label="新仓库编号"
                    />
                    <input
                      placeholder="仓库名称"
                      value={warehouseForm.warehouse_name}
                      onChange={(event) =>
                        setWarehouseForm({
                          ...warehouseForm,
                          warehouse_name: event.target.value,
                        })
                      }
                      maxLength={120}
                      aria-label="新仓库名称"
                    />
                    <input
                      type="number"
                      min="0"
                      step="1"
                      value={warehouseForm.qty_available}
                      onChange={(event) =>
                        setWarehouseForm({
                          ...warehouseForm,
                          qty_available: event.target.value,
                        })
                      }
                      aria-label="新仓库可售库存"
                    />
                    <button
                      className="btn btn-ghost btn-sm"
                      type="button"
                      disabled={busy}
                      onClick={() => void addWarehouse(product)}
                    >
                      新增/更新仓库
                    </button>
                  </div>
                ) : null}
              </div>
            </article>
          ))}
        </div>
      )}
    </section>
  );
}
