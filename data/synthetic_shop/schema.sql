-- 合成单店 schema（F-004）。单店、离线、合成数据，不接任何真实店铺（方案 §1.3 / R7）。
-- 金额一律以「分」为整数存储；任何需要计算金额的地方都由下面的 SQL 视图产出，
-- LLM 不参与计算（验收 A9 的结构性保证）。

PRAGMA foreign_keys = ON;

DROP TABLE IF EXISTS products;
DROP TABLE IF EXISTS inventory;
DROP TABLE IF EXISTS orders;
DROP TABLE IF EXISTS order_items;
DROP TABLE IF EXISTS shipments;
DROP TABLE IF EXISTS shipment_events;
DROP TABLE IF EXISTS kb_entries;
DROP TABLE IF EXISTS human_agents;

CREATE TABLE products (
    sku             TEXT PRIMARY KEY,
    title           TEXT NOT NULL,
    category        TEXT NOT NULL,
    brand           TEXT NOT NULL,
    specs           TEXT NOT NULL,
    price_cents     INTEGER NOT NULL CHECK (price_cents >= 0),
    listed_at       TEXT NOT NULL,
    support_7d_no_reason INTEGER NOT NULL DEFAULT 1  -- 是否支持 7 天无理由
);

CREATE TABLE inventory (
    sku            TEXT NOT NULL,
    region         TEXT NOT NULL,
    qty_available  INTEGER NOT NULL CHECK (qty_available >= 0),
    warehouse      TEXT NOT NULL,
    updated_at     TEXT NOT NULL,
    PRIMARY KEY (sku, region)
);

CREATE TABLE orders (
    order_id        TEXT PRIMARY KEY,
    customer_phone  TEXT NOT NULL,
    placed_at       TEXT NOT NULL,
    status          TEXT NOT NULL,       -- 待付款|已付款待发货|已发货|运输中|已签收|退款申请中|退款成功|已取消
    refund_status   TEXT NOT NULL,       -- 无|申请中|已受理待核|成功|驳回
    freight_cents   INTEGER NOT NULL DEFAULT 0,
    coupon_cents    INTEGER NOT NULL DEFAULT 0,
    pay_channel     TEXT NOT NULL,       -- 微信零钱|储蓄卡|信用卡|花呗
    address_city    TEXT NOT NULL,
    delivery_city   TEXT NOT NULL,
    refund_reason   TEXT,
    applied_at      TEXT
);

CREATE TABLE order_items (
    order_id         TEXT NOT NULL,
    sku              TEXT NOT NULL,
    qty              INTEGER NOT NULL CHECK (qty > 0),
    unit_price_cents INTEGER NOT NULL CHECK (unit_price_cents >= 0),
    PRIMARY KEY (order_id, sku),
    FOREIGN KEY (order_id) REFERENCES orders (order_id),
    FOREIGN KEY (sku) REFERENCES products (sku)
);

-- 商品总价：由 SQL 聚合产出，不由模型计算
CREATE VIEW v_order_item_total AS
SELECT order_id,
       SUM(qty * unit_price_cents) AS item_total_cents,
       SUM(qty)                    AS item_qty
FROM order_items
GROUP BY order_id;

-- 实付金额 = 商品总价 + 运费 - 优惠券（全部 SQL 确定性计算）
CREATE VIEW v_order_settlement AS
SELECT o.order_id,
       o.customer_phone,
       o.status,
       o.refund_status,
       o.pay_channel,
       t.item_total_cents,
       o.freight_cents,
       o.coupon_cents,
       (t.item_total_cents + o.freight_cents - o.coupon_cents) AS payable_cents
FROM orders o
JOIN v_order_item_total t ON t.order_id = o.order_id;

-- 可退上限：已付款状态下 = 实付金额；未付款/已取消 = 0
CREATE VIEW v_refund_cap AS
SELECT s.order_id,
       s.payable_cents,
       CASE
           WHEN s.status IN ('已付款待发货', '已发货', '运输中', '已签收', '退款申请中', '退款成功')
               THEN s.payable_cents
           ELSE 0
       END AS refundable_max_cents
FROM v_order_settlement s;

CREATE TABLE shipments (
    order_id      TEXT PRIMARY KEY,
    carrier       TEXT NOT NULL,
    tracking_no   TEXT NOT NULL,
    shipped_at    TEXT,
    delivered_at  TEXT,
    status        TEXT NOT NULL,   -- 待揽收|运输中|派送中|已签收|异常件
    current_node  TEXT,
    eta_days      INTEGER,
    FOREIGN KEY (order_id) REFERENCES orders (order_id)
);

CREATE TABLE shipment_events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id     TEXT NOT NULL,
    happened_at  TEXT NOT NULL,
    node         TEXT NOT NULL,
    description  TEXT NOT NULL
);

-- 政策/FAQ 双时态知识库（F-006）
CREATE TABLE kb_entries (
    entry_id     TEXT NOT NULL,
    version      TEXT NOT NULL,
    title        TEXT NOT NULL,
    body         TEXT NOT NULL,
    category     TEXT NOT NULL,
    topic        TEXT NOT NULL,
    source       TEXT NOT NULL,
    valid_from   TEXT NOT NULL,
    valid_until  TEXT,               -- NULL = 长期有效
    published_at TEXT NOT NULL,      -- 事务时间：本条进入知识库的时间
    supersedes   TEXT,
    PRIMARY KEY (entry_id, version)
);

CREATE TABLE human_agents (
    queue      TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    skill      TEXT NOT NULL,
    on_duty    INTEGER NOT NULL DEFAULT 1
);
