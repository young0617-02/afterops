export type Customer = {
  id: number;
  phone: string | null;
  name: string | null;
  email: string | null;
  notify_email_shipment: boolean;
  notify_email_after_sales: boolean;
  notify_email_return: boolean;
};

export type OtpResponse = {
  status: string;
  expires_in: number;
  debug_code: string | null;
};

export type ProductSummary = {
  sku: string;
  title: string;
  category: string;
  brand: string;
  price_cents: number;
  price_display: string;
  support_7d_no_reason: boolean;
  warranty_months: number;
  available_qty: number;
  in_stock: boolean;
  listed_at: string;
  image_url: string | null;
  variant_label: string;
};

export type InventoryRow = {
  warehouse_id: string;
  warehouse_name: string;
  qty_available: number;
};

export type ProductVariant = {
  sku: string;
  title: string;
  variant_label: string;
  price_cents: number;
  price_display: string;
  available_qty: number;
  in_stock: boolean;
  image_url: string | null;
};

export type ProductDetail = ProductSummary & {
  description: string;
  specs: string;
  gallery_urls: string[];
  inventory: InventoryRow[];
  variants: ProductVariant[];
};

export type CategoryCount = {
  category: string;
  count: number;
};

export type CartItem = {
  sku: string;
  title: string;
  qty: number;
  unit_price_cents: number;
  unit_price_display: string;
  line_total_cents: number;
  line_total_display: string;
  available_qty: number;
};

export type ShippingMethod = {
  id: number;
  code: string;
  name: string;
  fee_cents: number;
  free_over_cents: number | null;
  status: string;
  sort_order: number;
  created_at: string;
};

export type Cart = {
  cart_id: number;
  status: string;
  items: CartItem[];
  item_total_cents: number;
  item_total_display: string;
  freight_cents: number;
  freight_display: string;
  discount_cents: number;
  discount_display: string;
  payable_cents: number;
  payable_display: string;
};

export type OrderSummary = {
  id: number;
  order_no: string;
  status: string;
  payment_status: string;
  fulfillment_status: string;
  payable_cents: number;
  payable_display: string;
  discount_cents: number;
  discount_display: string;
  freight_cents: number;
  freight_display: string;
  currency: string;
  placed_at: string;
  item_count: number;
  cancel_requested_at: string | null;
};

export type OrderItem = {
  order_item_id: number;
  sku: string;
  title: string;
  qty: number;
  cancelled_qty: number;
  shipped_qty: number;
  cancelable_qty: number;
  unit_price_cents: number;
  unit_price_display: string;
  line_total_cents: number;
  line_total_display: string;
};

export type Payment = {
  id: number;
  provider: string;
  status: string;
  amount_cents: number;
  amount_display: string;
};

export type ShipmentEvent = {
  happened_at: string;
  node: string;
  description: string;
};

export type ShipmentItem = {
  sku: string;
  title: string;
  qty: number;
};

export type Shipment = {
  id: number;
  package_no: number;
  carrier: string;
  tracking_no: string;
  status: string;
  current_node: string | null;
  shipped_at: string | null;
  delivered_at: string | null;
  items: ShipmentItem[];
  events: ShipmentEvent[];
};

export type AuditEvent = {
  id: number;
  event_type: string;
  payload: Record<string, unknown>;
  conversation_id: number | null;
  proposal_id: number | null;
  prev_hash: string;
  row_hash: string;
  created_at: string;
};

export type AuditList = {
  items: AuditEvent[];
  has_more: boolean;
  next_before_id: number | null;
  event_types: string[];
  total: number;
};

export type AuditVerify = {
  valid: boolean;
  checked_events: number;
  broken_at: number | null;
};

export type AfterSalesItem = {
  id: number;
  action_type: string;
  status: string;
  order_no: string;
  amount_cents: number | null;
  amount_display: string | null;
  reason: string;
  message: string | null;
  commerce_ref: string | null;
  created_at: string;
  approved_at: string | null;
  executed_at: string | null;
  attachment_ids: number[];
};

export type AfterSalesTimelineEvent = {
  kind: string;
  occurred_at: string;
  title: string;
  detail: string;
  status: string;
  order_no: string | null;
  entity_id: number | null;
};

export type Overview = {
  generated_at: string;
  window_hours: number;
  pending_proposals: number;
  pending_handoffs: number;
  claimed_handoffs: number;
  urgent_handoffs: number;
  unfulfilled_orders: number;
  pending_returns: number;
  unread_notifications: number;
  paid_orders: number;
  paid_amount_cents: number;
  paid_amount_display: string;
  refunds: number;
  refund_amount_cents: number;
  refund_amount_display: string;
  proposals_created: number;
  proposals_executed: number;
  proposals_rejected: number;
  handoffs_created: number;
  handoffs_resolved: number;
  ai_chats: number;
  approval_median_seconds: number | null;
  audit_valid: boolean;
  audit_checked_events: number;
  audit_broken_at: number | null;
};

export type TrendDay = {
  date: string;
  paid_orders: number;
  paid_amount_cents: number;
  paid_amount_display: string;
  refunds: number;
  refund_amount_cents: number;
  refund_amount_display: string;
  proposals: number;
  handoffs: number;
  ai_chats: number;
};

export type NotificationItem = {
  id: number;
  type: string;
  title: string;
  body: string;
  link: string;
  read_at: string | null;
  created_at: string;
};

export type NotificationList = {
  items: NotificationItem[];
  unread_count: number;
};

export type CustomerHandoff = {
  id: number;
  ticket_ref: string;
  queue: string;
  status: string;
  intent: string;
  last_user_message: string;
  last_reply: string;
  created_at: string;
  updated_at: string;
  resolved_at: string | null;
  rating_score: number | null;
  rating_comment: string | null;
};

export type InvoiceView = {
  id: number;
  order_no: string;
  invoice_type: "personal" | "company";
  title: string;
  tax_no: string;
  email: string;
  amount_cents: number;
  net_paid_cents: number;
  adjustment_required: boolean;
  status: string;
  invoice_no: string;
  reject_reason: string;
  void_reason: string;
  created_at: string;
  issued_at: string | null;
  voided_at: string | null;
};

export type StockTransferView = {
  id: number;
  transfer_no: string;
  sku: string;
  title: string;
  from_warehouse_id: string;
  to_warehouse_id: string;
  qty: number;
  status: "in_transit" | "received" | "cancelled";
  note: string;
  created_by: number | null;
  created_at: string;
  received_at: string | null;
  cancelled_at: string | null;
  cancel_reason: string;
};

export type Coupon = {
  id: number;
  code: string;
  discount_type: "fixed" | "percent";
  value: number;
  min_spend_cents: number;
  starts_on: string;
  ends_on: string | null;
  max_uses: number | null;
  used_count: number;
  per_customer_limit: number;
  scope_type: "all" | "category" | "sku";
  scope_values: string[];
  claimable: boolean;
  claim_count: number;
  status: string;
  created_at: string;
};

export type ClaimableCoupon = {
  id: number;
  discount_type: "fixed" | "percent";
  value: number;
  min_spend_cents: number;
  starts_on: string;
  ends_on: string | null;
  scope_type: "all" | "category" | "sku";
  scope_values: string[];
  claimed: boolean;
  sold_out: boolean;
};

export type WalletCoupon = ClaimableCoupon & {
  code: string;
  status: string;
  claimed_at: string;
  times_used: number;
  remaining_uses: number;
  state: "active" | "used_up" | "expired" | "disabled";
};

export type ProductReview = {
  id: number;
  rating: number;
  content: string;
  customer_name: string;
  created_at: string;
  image_urls: string[];
  merchant_reply: string;
  replied_at: string | null;
  follow_up_content: string;
  follow_up_image_urls: string[];
  followed_up_at: string | null;
};

export type ProductReviews = {
  average: number | null;
  count: number;
  reviews: ProductReview[];
};

export type MyReview = {
  id: number;
  order_no: string;
  sku: string;
  product_title: string;
  rating: number;
  content: string;
  status: string;
  created_at: string;
  image_urls: string[];
  merchant_reply: string;
  replied_at: string | null;
  follow_up_content: string;
  follow_up_image_urls: string[];
  followed_up_at: string | null;
};

export type AdminReview = MyReview & {
  customer_name: string;
  customer_phone: string | null;
};

export type RepairView = {
  id: number;
  order_no: string;
  sku: string;
  title: string;
  status: string;
  issue: string;
  attachment_ids: number[];
  carrier: string;
  tracking_no: string;
  return_carrier: string;
  return_tracking_no: string;
  return_address: string;
  rejected_reason: string;
  resolution_note: string;
  quote_cents: number | null;
  quote_status: "none" | "pending" | "accepted" | "declined" | "paid";
  quote_note: string;
  quote_created_at: string | null;
  quote_revised_at: string | null;
  quote_responded_at: string | null;
  quote_paid_at: string | null;
  quote_payment_ref: string;
  parts_cost_cents: number | null;
  parts_note: string | null;
  created_at: string;
  approved_at: string | null;
  received_at: string | null;
  repaired_at: string | null;
  completed_at: string | null;
};

export type RepairEligibilityItem = {
  sku: string;
  title: string;
  warranty_months: number;
  deadline: string | null;
  eligible: boolean;
  reason: string;
};

export type RepairEligibility = {
  order_no: string;
  delivered_at: string | null;
  items: RepairEligibilityItem[];
};

export type ReturnAnalytics = {
  days: number;
  since: string;
  total: number;
  status_counts: Record<string, number>;
  reasons: { category: string; label: string; count: number }[];
  top_skus: { sku: string; title: string; requests: number; qty: number }[];
  responsibilities: {
    responsibility: string;
    label: string;
    items: number;
    qty: number;
    deduction_cents: number;
  }[];
  refund_total_cents: number;
  approval_rate: number;
  avg_approval_hours: number | null;
  restocked_units: number;
  damaged_units: number;
  damaged_cost_cents: number;
};

export type ReplacementShipmentView = {
  id: number;
  package_no: number;
  carrier: string;
  tracking_no: string;
  status: string;
  current_node: string | null;
  shipped_at: string | null;
  delivered_at: string | null;
  events: { node: string; description: string; happened_at: string }[];
};

export type ReturnAppealView = {
  id: number;
  status: "pending" | "accepted" | "rejected" | "withdrawn";
  reason: string;
  attachment_ids: number[];
  requested_cents: number;
  approved_cents: number | null;
  resolution_note: string;
  created_at: string;
  resolved_at: string | null;
};

export type ReturnPickupView = {
  id: number;
  pickup_no: string;
  status: "scheduled" | "picked_up" | "cancelled";
  scheduled_date: string;
  time_window: string;
  address_recipient: string;
  address_phone: string;
  address_region: string;
  address_city: string;
  address_detail: string;
  courier_name: string;
  courier_phone: string;
  cancel_reason: string;
  picked_up_at: string | null;
  cancelled_at: string | null;
  created_at: string;
};

export type ReturnRequestView = {
  id: number;
  order_no: string;
  customer_phone: string | null;
  customer_risk_level: "low" | "medium" | "high" | null;
  customer_risk_reasons: string[];
  status: string;
  reason: string;
  reason_category: string;
  resolution: "refund" | "exchange";
  exchange_sku: string | null;
  exchange_unit_price_cents: number | null;
  replacement_order_no: string | null;
  replacement_shipments: ReplacementShipmentView[];
  exchange_supplement_cents: number;
  exchange_payment_status: "none" | "pending" | "paid" | "cancelled";
  exchange_payment_ref: string;
  exchange_paid_at: string | null;
  exchange_cancelled_at: string | null;
  exchange_cancel_reason: string;
  exchange_cancelled_by: string;
  exchange_payment_deadline: string | null;
  appeal: ReturnAppealView | null;
  appeal_deadline: string | null;
  can_appeal: boolean;
  pickup: ReturnPickupView | null;
  can_schedule_pickup: boolean;
  return_address: string;
  carrier: string;
  tracking_no: string;
  shipment_status: string;
  tracking_events: {
    status: string;
    label: string;
    description: string;
    happened_at: string;
  }[];
  rejected_reason: string;
  refund_proposal_id: number | null;
  attachment_ids: number[];
  inspection_attachment_ids: number[];
  created_at: string;
  approved_at: string | null;
  received_at: string | null;
  refund_amount_cents: number | null;
  inspection_deduction_cents: number;
  inspection_note: string;
  items: {
    id: number;
    sku: string;
    title: string;
    qty: number;
    exchange_sku: string | null;
    exchange_unit_price_cents: number | null;
    disposition: "restock" | "damaged" | null;
    inspection_deduction_cents: number;
    responsibility:
      | "merchant"
      | "customer"
      | "logistics"
      | "other"
      | null;
  }[];
  restocked_items?: { sku: string; warehouse_id: string; qty: number }[];
  damaged_items?: { sku: string; title: string; qty: number }[];
};

export type HelpPolicy = {
  entry_id: string;
  topic: string;
  title: string;
  body: string;
  version: string;
  source: string;
  valid_from: string;
  valid_until: string | null;
  published_at: string;
};

export type HelpCenter = {
  policies: HelpPolicy[];
  return_policy: {
    window_days: number;
    return_shipping_fee_cents: number;
    allow_exchange: boolean;
    final_sale_categories: string[];
    category_windows: { category: string; window_days: number }[];
  };
};

export type ReturnPolicy = {
  window_days: number;
  return_shipping_fee_cents: number;
  allow_exchange: boolean;
  final_sale_categories: string[];
  category_windows: { category: string; window_days: number }[];
  updated_at: string | null;
};

export type ReturnEligibilityItem = {
  sku: string;
  title: string;
  category: string;
  window_days: number;
  deadline: string | null;
  eligible: boolean;
  reason: string;
};

export type ReturnEligibility = {
  eligible: boolean;
  reason: string;
  window_days: number;
  return_shipping_fee_cents: number;
  delivered_at: string | null;
  deadline: string | null;
  allow_exchange: boolean;
  final_sale_skus: string[];
  items: ReturnEligibilityItem[];
};

export type PolicyVersion = {
  entry_id: string;
  topic: string;
  title: string;
  body: string;
  version: string;
  source: string;
  valid_from: string;
  valid_until: string | null;
  published_at: string;
};

export type PolicyGroup = {
  entry_id: string;
  topic: string;
  current: PolicyVersion | null;
  versions: PolicyVersion[];
};

export type InventoryDiscrepancy = {
  product_id: number;
  sku: string;
  product_title: string;
  warehouse_id: string;
  warehouse_name: string;
  qty_available: number;
  movement_total: number;
  delta: number;
};

export type InventoryValuation = {
  total_qty: number;
  total_value_cents: number;
  lines: {
    sku: string;
    title: string;
    warehouse_id: string;
    warehouse_name: string;
    qty_available: number;
    qty_reserved: number;
    qty_on_hand: number;
    cost_cents: number;
    value_cents: number;
  }[];
};

export type InventoryReconciliation = {
  tracked: number;
  matched: number;
  untracked: number;
  discrepancies: InventoryDiscrepancy[];
};

export type InventoryMovement = {
  id: number;
  sku: string;
  product_title: string;
  warehouse_id: string;
  warehouse_name: string;
  qty_delta: number;
  qty_after: number;
  reason: string;
  order_no: string | null;
  return_id: number | null;
  operator_email: string | null;
  note: string;
  created_at: string;
};

export type AgentPerformanceRow = {
  user_id: number;
  email: string;
  role: string;
  status: string;
  open_tickets: number;
  claimed: number;
  resolved: number;
  avg_claim_minutes: number | null;
  avg_resolution_minutes: number | null;
  first_responses: number;
  avg_first_response_minutes: number | null;
  sla_breaches: number;
  resolution_sla_breaches: number;
  first_response_sla_breaches: number;
  csat_count: number;
  csat_average: number | null;
};

export type RecentSatisfaction = {
  ticket_id: number;
  ticket_ref: string;
  customer_phone: string | null;
  agent_email: string | null;
  score: number;
  comment: string;
  created_at: string;
};

export type AgentPerformance = {
  hours: number;
  since: string;
  unassigned_open: number;
  csat_count: number;
  csat_average: number | null;
  agents: AgentPerformanceRow[];
  recent_ratings: RecentSatisfaction[];
};

export type StaffMember = {
  id: number;
  email: string;
  role: "owner" | "agent";
  status: string;
  handoff_available: boolean;
  last_seen_at: string | null;
  online: boolean;
  handoff_skills: string[];
  created_at: string;
};

export type HandoffSettings = {
  auto_assign_handoffs: boolean;
};

export type QuickReply = {
  id: number;
  title: string;
  category: string;
  content: string;
  status: string;
  created_by_email: string | null;
  created_at: string;
  updated_at: string;
};

export type OutboxEvent = {
  id: number;
  event_type: string;
  aggregate_type: string;
  aggregate_id: string;
  to: string;
  subject: string;
  status: string;
  attempts: number;
  last_error: string;
  available_at: string;
  created_at: string;
  processed_at: string | null;
};

export type OutboxList = {
  items: OutboxEvent[];
  counts: Record<string, number>;
};

export type AdminReturnList = {
  items: ReturnRequestView[];
  counts: Record<string, number>;
};

export type AdminProduct = {
  sku: string;
  title: string;
  category: string;
  brand: string;
  description: string;
  specs: string;
  price_cents: number;
  price_display: string;
  cost_cents: number | null;
  cost_display: string | null;
  variant_group: string;
  variant_label: string;
  support_7d_no_reason: boolean;
  warranty_months: number;
  status: string;
  listed_at: string;
  available_qty: number;
  reserved_qty: number;
  image_url: string | null;
  gallery: {
    storage_name: string;
    content_type: string;
    size_bytes: number;
    url: string;
  }[];
  inventory: {
    warehouse_id: string;
    warehouse_name: string;
    qty_available: number;
    qty_reserved: number;
  }[];
};

export type AdminOrder = {
  order_no: string;
  status: string;
  payment_status: string;
  fulfillment_status: string;
  payable_cents: number;
  payable_display: string;
  placed_at: string;
  recipient_name: string;
  recipient_phone: string;
  address: string;
  item_count: number;
  items: {
    order_item_id: number;
    sku: string;
    title: string;
    qty: number;
    shipped_qty: number;
    cancelled_qty: number;
    remaining_qty: number;
  }[];
  shipments: Shipment[];
};

export type Address = {
  recipient_name: string;
  recipient_phone: string;
  region: string;
  city: string;
  detail: string;
};

export type CustomerAddress = Address & {
  id: number;
  label: string;
  is_default: boolean;
  created_at: string;
  updated_at: string;
};

export type RefundRecord = {
  id: number;
  kind: string;
  amount_cents: number;
  amount_display: string;
  status: string;
  reason: string;
  provider_refund_id: string;
  settlement_status: "processing" | "succeeded" | "failed";
  settlement_reference: string;
  settlement_failure_reason: string;
  settled_at: string | null;
  created_at: string;
  executed_at: string | null;
};

export type RefundSettlementRecord = RefundRecord & {
  order_no: string;
  proposal_id: number;
  settlement_attempts: number;
};

export type OrderDetail = OrderSummary & {
  items: OrderItem[];
  payment: Payment | null;
  refunds: RefundRecord[];
  refunded_total_cents: number;
  refunded_total_display: string;
  net_paid_cents: number;
  net_paid_display: string;
  shipments: Shipment[];
  address: Address;
};

export type CancelRequestResult = {
  order_no: string;
  proposal_id: number;
  amount_cents: number;
  amount_display: string;
  status: string;
  scope: "full" | "partial";
  items: { sku: string; qty: number }[];
};

export type CheckoutResult = {
  order_no: string;
  payment_id: number;
  amount_cents: number;
  amount_display: string;
  status: string;
  expires_at: string | null;
  discount_cents: number;
  discount_display: string;
  coupon_code: string | null;
  freight_cents: number;
  freight_display: string;
  shipping_method_code: string | null;
  shipping_method_name: string | null;
};

export type Proposal = {
  id: number;
  action_type: string;
  status: string;
  risk_level: string;
  order_no: string | null;
  amount_cents: number | null;
  amount_display: string | null;
  created_by: string;
  created_at: string;
  approved_by: number | null;
  commerce_ref: string | null;
  attachment_ids: number[];
  reason: string;
  cancel_scope: "full" | "partial" | null;
  cancel_items: { order_item_id: number; sku: string; qty: number }[];
};

export type ChatProposal = {
  id: number;
  action_type: string;
  status: string;
  risk_level: string;
  order_no: string;
  amount_cents: number | null;
};

export type ChatReturn = {
  id: number;
  status: string;
  resolution: string;
};

export type ChatToolCalls = {
  citations?: string[];
  proposals?: ChatProposal[];
  returns?: ChatReturn[];
  degraded?: boolean;
  fallback_reason?: string | null;
  intent?: string;
  audit_id?: number;
  handoff_ticket_ref?: string | null;
};

export type ChatHistoryMessage = {
  id: number;
  role: string;
  content: string;
  tool_calls_json: ChatToolCalls | null;
  image_urls: string[];
  created_at: string;
};

export type ConversationSummary = {
  id: number;
  status: string;
  created_at: string;
  updated_at: string;
  last_message: string | null;
};

export type ConversationDetail = {
  id: number;
  status: string;
  created_at: string;
  updated_at: string;
  messages: ChatHistoryMessage[];
};

export type ChatResponse = {
  conversation_id: number;
  message_id: number;
  reply: string;
  intent: string;
  intent_label: string;
  confidence: number;
  citations: string[];
  handoff: {
    available: boolean;
    queue: string;
    ticket_ref: string | null;
  };
  proposals: ChatProposal[];
  returns: ChatReturn[];
  audit_id: number;
  provider: string;
  degraded: boolean;
  fallback_reason: string | null;
};

export type HandoffTicket = {
  id: number;
  ticket_ref: string;
  queue: string;
  status: string;
  priority: string;
  intent: string;
  last_user_message: string;
  last_reply: string;
  summary: string;
  summary_generated_at: string | null;
  customer_phone: string | null;
  customer_name: string | null;
  conversation_id: number;
  claimed_by_user_id: number | null;
  claimed_by_email: string | null;
  claimed_at: string | null;
  resolved_by_user_id: number | null;
  resolved_by_email: string | null;
  resolved_at: string | null;
  resolution_note: string;
  escalated_at: string | null;
  created_at: string;
  updated_at: string;
};

export type HandoffList = {
  items: HandoffTicket[];
  counts: Record<string, number>;
};

export type HandoffMessage = {
  id: number;
  role: string;
  content: string;
  image_urls: string[];
  created_at: string;
};

export type HandoffNote = {
  id: number;
  author_email: string | null;
  content: string;
  created_at: string;
};

export type HandoffCustomerContext = {
  order_count: number;
  last_order_at: string | null;
  after_sales_count: number;
  pending_proposals: number;
  active_returns: number;
  recent_orders: {
    order_no: string;
    status: string;
    payment_status: string;
    fulfillment_status: string;
    payable_cents: number;
    payable_display: string;
    placed_at: string;
  }[];
};

export type HandoffDetail = HandoffTicket & {
  messages: HandoffMessage[];
  notes: HandoffNote[];
  customer_context: HandoffCustomerContext;
};

export type AdminMe = {
  user_id: number;
  email: string;
  role: "owner" | "agent";
  handoff_available: boolean;
};

export type ApiErrorBody = {
  detail?: string;
  code?: string;
};

export type CustomerDirectoryRow = {
  id: number;
  phone: string | null;
  name: string | null;
  email: string | null;
  created_at: string;
  order_count: number;
  last_order_at: string | null;
  after_sales_count: number;
  pending_proposals: number;
  active_returns: number;
};

export type CustomerDirectoryDetail = CustomerDirectoryRow & {
  orders: {
    order_no: string;
    status: string;
    payment_status: string;
    fulfillment_status: string;
    payable_cents: number;
    payable_display: string;
    placed_at: string;
  }[];
  proposals: {
    id: number;
    action_type: string;
    status: string;
    amount_cents: number | null;
    amount_display: string | null;
    order_no: string | null;
    created_at: string;
  }[];
  returns: {
    id: number;
    status: string;
    resolution: string;
    order_no: string;
    created_at: string;
  }[];
  after_sales_risk: {
    level: "low" | "medium" | "high";
    reasons: string[];
    window_days: number;
    order_count: number;
    return_count: number;
    return_rate: number;
    cancelled_exchange_count: number;
    appeal_count: number;
    customer_responsibility_count: number;
    last_return_at: string | null;
  };
};

export type FinanceAnomaly = {
  order_no: string;
  kind: string;
  detail: string;
};

export type FinanceLedgerRow = {
  at: string;
  kind: string;
  order_no: string;
  amount_cents: number;
  amount_display: string;
};

export type FinanceSummary = {
  window_days: number;
  paid_count: number;
  paid_amount_cents: number;
  paid_amount_display: string;
  refund_count: number;
  refund_amount_cents: number;
  refund_amount_display: string;
  compensation_count: number;
  compensation_amount_cents: number;
  compensation_amount_display: string;
  net_amount_cents: number;
  net_amount_display: string;
  pending_proposal_count: number;
  pending_proposal_amount_cents: number;
  pending_proposal_amount_display: string;
  refund_auto_settle_minutes: number;
  refund_auto_retry_minutes: number;
  refund_auto_retry_max_attempts: number;
  anomalies: FinanceAnomaly[];
  ledger: FinanceLedgerRow[];
};

export type AfterSalesCharge = {
  id: number;
  kind: "exchange_supplement" | "repair_fee";
  order_no: string;
  customer_phone: string | null;
  return_request_id: number | null;
  repair_request_id: number | null;
  amount_cents: number;
  amount_display: string;
  reference: string;
  created_at: string;
};

export type AfterSalesChargeLedger = {
  items: AfterSalesCharge[];
  count: number;
  exchange_supplement_cents: number;
  exchange_supplement_display: string;
  repair_fee_cents: number;
  repair_fee_display: string;
  repair_parts_cost_cents: number;
  repair_parts_cost_display: string;
  repair_margin_cents: number;
  repair_margin_display: string;
  total_cents: number;
  total_display: string;
  days: number;
};

export type AuditAnchor = {
  merchant_id: number;
  audit_id: number;
  row_hash: string;
  prev_anchor_hash: string;
  anchored_at: string;
  signature: string | null;
  signature_valid: boolean;
  prev_valid: boolean;
  row_match: boolean;
  valid: boolean;
};

export type AuditAnchorStatus = {
  configured: boolean;
  count: number;
  chain_valid: boolean;
  file_errors: number;
  anchored_through_id: number | null;
  current_head_id: number | null;
  current_head_hash: string | null;
  head_covered: boolean;
  anchors: AuditAnchor[];
};

export type AdminOrderDetail = {
  order_no: string;
  status: string;
  payment_status: string;
  fulfillment_status: string;
  customer_id: number;
  customer_name: string | null;
  customer_phone: string | null;
  recipient_name: string;
  recipient_phone: string;
  region: string;
  city: string;
  detail: string;
  item_total_display: string;
  freight_display: string;
  discount_display: string;
  payable_display: string;
  currency: string;
  placed_at: string;
  items: {
    sku: string;
    title: string;
    qty: number;
    cancelled_qty: number;
    unit_price_display: string;
    line_total_cents: number;
    line_total_display: string;
  }[];
  payments: {
    id: number;
    provider: string;
    provider_payment_id: string;
    amount_display: string;
    status: string;
    created_at: string;
    paid_at: string | null;
  }[];
  refunds: {
    id: number;
    proposal_id: number;
    kind: string;
    amount_display: string;
    status: string;
    provider_refund_id: string;
    created_at: string;
    executed_at: string | null;
  }[];
  shipments: {
    id: number;
    package_no: number;
    carrier: string;
    tracking_no: string;
    status: string;
    current_node: string | null;
    created_at: string;
    delivered_at: string | null;
    items: {
      sku: string;
      title: string;
      qty: number;
    }[];
    events: {
      node: string;
      description: string;
      happened_at: string;
    }[];
  }[];
  proposals: {
    id: number;
    action_type: string;
    status: string;
    risk_level: string;
    amount_display: string | null;
    created_by: string;
    created_at: string;
    commerce_ref: string | null;
    error: string | null;
  }[];
  returns: {
    id: number;
    status: string;
    resolution: string;
    reason: string;
    exchange_sku: string | null;
    created_at: string;
  }[];
};
