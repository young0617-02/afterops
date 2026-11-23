const ORDER_STATUS: Record<string, string> = {
  pending_payment: "待支付",
  paid: "已支付",
  fulfilling: "履约中",
  shipped: "已发货",
  delivered: "已签收",
  completed: "已完成",
  cancelled: "已取消",
  refunding: "退款处理中",
  refunded: "已退款",
  partially_refunded: "部分退款",
};

const PAYMENT_STATUS: Record<string, string> = {
  unpaid: "未支付",
  pending: "支付处理中",
  paid: "已支付",
  failed: "支付失败",
  refunded: "已退款",
  partially_refunded: "部分退款",
};

const FULFILLMENT_STATUS: Record<string, string> = {
  unfulfilled: "未履约",
  allocated: "已分配库存",
  shipped: "已发货",
  delivered: "已签收",
};

const SHIPMENT_STATUS: Record<string, string> = {
  pending: "待揽收",
  in_transit: "运输中",
  out_for_delivery: "派送中",
  delivered: "已签收",
  exception: "异常件",
};

const PROPOSAL_STATUS: Record<string, string> = {
  pending: "待审批",
  approved: "已批准",
  rejected: "已驳回",
  executing: "执行中",
  executed: "已执行",
  failed: "执行失败",
  expired: "已过期",
  withdrawn: "已撤回",
};

const ACTION_TYPE: Record<string, string> = {
  refund: "退款",
  compensation: "补偿",
  address_change: "改地址",
  cancel_order: "取消订单",
  handoff: "转人工",
};

const RISK_LEVEL: Record<string, string> = {
  L1: "低风险",
  L2: "资金动作",
  L3: "高危",
};

const AUDIT_EVENT_TYPE: Record<string, string> = {
  order_created: "订单创建",
  payment_paid: "支付成功",
  order_cancelled: "订单取消",
  order_completed: "订单完成",
  order_expired: "订单过期",
  chat_answered: "AI 回复",
  proposal_created: "提案创建",
  proposal_approved: "提案批准",
  proposal_rejected: "提案驳回",
  proposal_executed: "提案执行",
  proposal_withdrawn: "提案撤回",
  shipment_created: "运单创建",
  shipment_status_changed: "物流更新",
  return_requested: "退货申请",
  return_approved: "退货批准",
  return_rejected: "退货驳回",
  return_tracking_added: "退货寄回",
  return_received: "退货收货",
  staff_created: "员工创建",
  staff_updated: "员工更新",
  staff_password_reset: "员工密码重置",
  proposal_sla_reminder: "提案超时提醒",
  handoff_sla_reminder: "工单超时提醒",
  handoff_created: "人工工单创建",
  handoff_claimed: "工单接管",
  handoff_resolved: "工单解决",
  product_created: "商品创建",
  product_updated: "商品更新",
  inventory_updated: "库存更新",
};

const PRODUCT_STATUS: Record<string, string> = {
  draft: "草稿",
  active: "在售",
  archived: "已下架",
};

const STAFF_ROLE: Record<string, string> = {
  owner: "店主",
  agent: "客服",
};

const STAFF_STATUS: Record<string, string> = {
  active: "启用",
  disabled: "已停用",
};

const OUTBOX_STATUS: Record<string, string> = {
  pending: "待发送",
  processing: "投递中",
  processed: "已投递",
  failed: "失败",
};

const RETURN_STATUS: Record<string, string> = {
  requested: "待审核",
  approved: "待回寄",
  in_transit: "已寄回",
  received: "已收货",
  rejected: "已驳回",
  withdrawn: "已撤回",
  exchange_cancelled: "换货已取消",
};

const INVOICE_STATUS: Record<string, string> = {
  requested: "待开票",
  issued: "已开票",
  rejected: "已驳回",
  voided: "已作废",
};

const REPAIR_STATUS: Record<string, string> = {
  requested: "待审核",
  approved: "待回寄",
  in_transit: "回寄中",
  received: "维修中",
  return_shipped: "维修寄回中",
  completed: "已完成",
  rejected: "已驳回",
  withdrawn: "已撤回",
};

const REPAIR_QUOTE_STATUS: Record<string, string> = {
  none: "未报价",
  pending: "待顾客确认",
  accepted: "待顾客支付",
  declined: "顾客已拒绝",
  paid: "维修费已支付",
};

const HANDOFF_STATUS: Record<string, string> = {
  open: "待接管",
  claimed: "处理中",
  resolved: "已解决",
};

const HANDOFF_PRIORITY: Record<string, string> = {
  normal: "普通",
  high: "高优先级",
  urgent: "紧急",
};

const HANDOFF_QUEUE: Record<string, string> = {
  after_sales: "售后",
  complaint: "投诉",
};

const HANDOFF_INTENT: Record<string, string> = {
  HUMAN_REQUEST: "人工转接",
  HIGH_RISK: "重大事项/承诺索取",
  COMPLAINT: "投诉升级",
  COMPENSATION_REQUEST: "补偿申请",
  REFUND_REQUEST: "退款申请",
  ADDRESS_CHANGE: "改地址申请",
  ORDER_STATUS: "订单查询",
  LOGISTICS_TRACK: "物流查询",
  RETURN_POLICY: "政策咨询",
  PRODUCT_INFO: "商品咨询",
  LOW_CSAT: "低分回访",
};

export const orderStatusLabel = (value: string) => ORDER_STATUS[value] ?? value;
export const paymentStatusLabel = (value: string) => PAYMENT_STATUS[value] ?? value;
export const fulfillmentStatusLabel = (value: string) =>
  FULFILLMENT_STATUS[value] ?? value;
export const shipmentStatusLabel = (value: string) => SHIPMENT_STATUS[value] ?? value;
export const proposalStatusLabel = (value: string) => PROPOSAL_STATUS[value] ?? value;
export const actionTypeLabel = (value: string) => ACTION_TYPE[value] ?? value;
export const riskLevelLabel = (value: string) => RISK_LEVEL[value] ?? value;
export const auditEventTypeLabel = (value: string) =>
  AUDIT_EVENT_TYPE[value] ?? value;
export const productStatusLabel = (value: string) =>
  PRODUCT_STATUS[value] ?? value;
export const staffRoleLabel = (value: string) =>
  STAFF_ROLE[value] ?? value;
export const staffStatusLabel = (value: string) =>
  STAFF_STATUS[value] ?? value;
export const outboxStatusLabel = (value: string) =>
  OUTBOX_STATUS[value] ?? value;
export const returnStatusLabel = (value: string) =>
  RETURN_STATUS[value] ?? value;

const RETURN_REASON: Record<string, string> = {
  quality: "质量问题",
  damaged: "运输破损",
  wrong_item: "发错/少发",
  size: "尺寸不合适",
  not_needed: "不想要",
  other: "其他",
};

export const returnReasonLabel = (value: string) =>
  RETURN_REASON[value] ?? value;
export const repairStatusLabel = (value: string) =>
  REPAIR_STATUS[value] ?? value;
export const repairQuoteStatusLabel = (value: string) =>
  REPAIR_QUOTE_STATUS[value] ?? value;
export const invoiceStatusLabel = (value: string) =>
  INVOICE_STATUS[value] ?? value;
export const handoffStatusLabel = (value: string) =>
  HANDOFF_STATUS[value] ?? value;
export const handoffPriorityLabel = (value: string) =>
  HANDOFF_PRIORITY[value] ?? value;
export const handoffQueueLabel = (value: string) =>
  HANDOFF_QUEUE[value] ?? value;
export const handoffIntentLabel = (value: string) =>
  HANDOFF_INTENT[value] ?? value;

export function formatDateOnly(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleDateString("zh-CN");
}

export function formatDateTime(value: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("zh-CN", { hour12: false });
}

export function badgeTone(status: string): string {
  if (["paid", "executed", "delivered", "completed", "approved", "received"].includes(status)) {
    return "badge-ok";
  }
  if (["pending_payment", "pending", "refunding", "reviewing", "executing", "requested", "in_transit"].includes(status)) {
    return "badge-warn";
  }
  if (["cancelled", "failed", "rejected", "refunded", "partially_refunded", "withdrawn"].includes(status)) {
    return "badge-muted";
  }
  return "badge-neutral";
}
