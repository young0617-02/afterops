from __future__ import annotations

from dataclasses import dataclass

from app.ai.guardrails import is_high_risk


@dataclass(frozen=True)
class Intent:
    code: str
    label: str
    confidence: float
    topic: str | None = None
    high_risk: bool = False


HUMAN_WORDS = ("转人工", "人工", "真人", "找客服", "客服在吗")
REFUND_STATUS_WORDS = ("退款到账", "退款进度", "退款状态", "多久到账", "钱什么时候退", "几天到账")
REFUND_ACTION_WORDS = (
    "我要退款", "申请退款", "退款吧", "给我退", "全额退", "直接退", "退给我", "必须退",
)
COMPENSATION_WORDS = ("赔偿", "赔付", "补偿", "赔我", "赔钱", "该赔", "得赔", "赔多少")
ADDRESS_WORDS = ("改地址", "修改地址", "换地址", "地址改成", "地址改为")
RETURN_ACTION_WORDS = (
    "我要退货", "申请退货", "帮我退货", "办理退货",
    "我要换货", "申请换货", "帮我换货", "办理换货",
)
INVOICE_STATUS_WORDS = (
    "发票进度", "发票状态", "发票到哪", "发票开好", "发票开出",
    "发票没收到", "发票还没", "我的发票",
)
REPAIR_STATUS_WORDS = (
    "维修进度", "维修状态", "修好了", "维修到哪", "维修寄回",
    "维修单号", "维修完成了吗",
)
RETURN_STATUS_WORDS = (
    "退货进度", "退货状态", "退货到哪", "退货处理", "退货退款进度",
)
COMPLAINT_WORDS = ("投诉", "曝光", "律师", "12315", "12345")
LOGISTICS_WORDS = ("物流", "快递", "到哪", "运单", "签收", "派送", "发货")
ORDER_WORDS = ("订单", "实付", "金额", "状态", "付款", "支付", "下单")
POLICY_WORDS = (
    "退货", "换货", "能退", "可以退", "怎么退", "无理由", "寄回", "保修", "质保",
    "维修", "运费", "包邮", "邮费", "发票", "开票", "改地址", "修改地址", "换地址",
)
PRODUCT_WORDS = ("sku-", "多少钱", "价格", "库存", "有货", "参数", "推荐", "哪款")


def _policy_topic(text: str) -> str | None:
    if any(word in text for word in ("退货", "换货", "能退", "可以退", "怎么退", "无理由", "寄回")):
        return "return"
    if any(word in text for word in ("退款到账", "多久到账", "几天到账", "钱什么时候退")):
        return "refund_time"
    if any(word in text for word in ("保修", "质保", "维修")):
        return "warranty"
    if any(word in text for word in ("运费", "包邮", "邮费")):
        return "freight"
    if any(word in text for word in ("发票", "开票")):
        return "invoice"
    if any(word in text for word in ("改地址", "修改地址", "换地址")):
        return "address_change"
    return None


def classify(text: str) -> Intent:
    normalized = text.strip().lower()

    if any(word in normalized for word in HUMAN_WORDS):
        return Intent("HUMAN_REQUEST", "人工转接", 0.95)

    if any(word in normalized for word in REFUND_STATUS_WORDS):
        return Intent("REFUND_STATUS", "退款进度", 0.8)

    if any(word in normalized for word in ("保证", "承诺", "给个准话", "你就说")):
        return Intent("HIGH_RISK", "重大事项/承诺索取", 0.9, None, True)

    if any(word in normalized for word in ADDRESS_WORDS):
        return Intent("ADDRESS_CHANGE", "改地址申请", 0.85)

    if any(word in normalized for word in COMPENSATION_WORDS):
        return Intent("COMPENSATION_REQUEST", "补偿申请", 0.9, "compensation", True)

    if any(word in normalized for word in COMPLAINT_WORDS):
        return Intent("COMPLAINT", "投诉升级", 0.9, "human", True)

    if any(word in normalized for word in REFUND_ACTION_WORDS):
        return Intent("REFUND_REQUEST", "退款申请", 0.9, "procedure", True)

    if is_high_risk(normalized):
        if any(word in normalized for word in ("赔偿", "赔付", "补偿")):
            topic = "compensation"
        elif any(word in normalized for word in ("价保", "补差价", "退差价")):
            topic = "price_protect"
        elif any(word in normalized for word in ("投诉", "曝光", "律师", "12315", "12345")):
            topic = "human"
        else:
            topic = None
        return Intent("HIGH_RISK", "重大事项/承诺索取", 0.9, topic, True)

    if any(word in normalized for word in RETURN_ACTION_WORDS):
        return Intent("RETURN_REQUEST", "退货/换货申请", 0.9, "return")

    if any(word in normalized for word in INVOICE_STATUS_WORDS):
        return Intent("INVOICE_STATUS", "发票进度", 0.85)

    if any(word in normalized for word in REPAIR_STATUS_WORDS):
        return Intent("REPAIR_STATUS", "维修进度", 0.85)

    if any(word in normalized for word in RETURN_STATUS_WORDS):
        return Intent("RETURN_STATUS", "退货进度", 0.85)

    if any(word in normalized for word in LOGISTICS_WORDS):
        return Intent("LOGISTICS_TRACK", "物流查询", 0.85)

    if any(word in normalized for word in ORDER_WORDS):
        return Intent("ORDER_STATUS", "订单查询", 0.8)

    if any(word in normalized for word in POLICY_WORDS):
        return Intent("RETURN_POLICY", "政策咨询", 0.8, _policy_topic(normalized) or "return")

    if any(word in normalized for word in PRODUCT_WORDS):
        return Intent("PRODUCT_INFO", "商品咨询", 0.8)

    if any(word in normalized for word in ("你好", "在吗", "谢谢", "再见")):
        return Intent("GREETING", "问候", 0.6)

    return Intent("UNKNOWN", "未识别", 0.3)
