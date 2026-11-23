import { expect, test, type Page } from "@playwright/test";

const PNG = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8" +
    "/x8AAwMCAO+/p9sAAAAASUVORK5CYII=",
  "base64",
);

function uniquePhone(offset: number): string {
  const base = Date.now() % 100_000_000;
  return `139${String((base + offset * 104_729) % 100_000_000).padStart(8, "0")}`;
}

async function loginCustomer(page: Page, phone: string): Promise<void> {
  await page.goto("/");
  await page.getByTestId("login-phone").fill(phone);
  const otpResponsePromise = page.waitForResponse(
    (response) =>
      response.url().includes("/api/store/otp/request") &&
      response.request().method() === "POST",
  );
  await page.getByTestId("otp-request").click();
  const otpResponse = await otpResponsePromise;
  const otp = (await otpResponse.json()) as { debug_code: string };
  await page.getByTestId("login-code").fill(otp.debug_code);
  await page.getByTestId("login-submit").click();
  await expect(page.getByText(phone)).toBeVisible();
  await expect(page.getByTestId("notification-bell")).toBeVisible();
}

async function addToCart(page: Page): Promise<void> {
  const preferred = page.getByTestId("add-SKU-EAR-001");
  if ((await preferred.count()) > 0 && (await preferred.isEnabled())) {
    await preferred.click();
  } else {
    await page.locator('[data-testid^="add-"]:not([disabled])').first().click();
  }
  await expect(page.getByText("已加入购物车")).toBeVisible();
}

async function checkout(
  page: Page,
  phone: string,
  paid: boolean,
  couponCode?: string,
  shippingCode?: string,
): Promise<string> {
  await page.goto("/cart");
  await page.locator("#recipient_name").fill("E2E 顾客");
  await page.locator("#recipient_phone").fill(phone);
  await page.locator("#region").fill("华东");
  await page.locator("#city").fill("上海");
  await page.locator("#detail").fill("E2E 测试路 1 号");
  if (couponCode) {
    await page.getByTestId("coupon-code").fill(couponCode);
  }
  if (shippingCode) {
    await page.getByTestId("shipping-method").selectOption(shippingCode);
  }
  await page.getByTestId("checkout-submit").click();
  await expect(page.getByText("订单已创建，请完成沙箱支付。")).toBeVisible();

  const orderText = await page
    .locator("text=/ORD-\\d{8}-[A-Z0-9]+/")
    .first()
    .textContent();
  const orderNo = orderText?.match(/ORD-\d{8}-[A-Z0-9]+/)?.[0];
  expect(orderNo).toBeTruthy();

  if (paid) {
    await page.getByTestId("mock-pay").click();
    await expect(page.getByRole("button", { name: "支付已确认" })).toBeVisible();
  }

  await page.getByRole("link", { name: "我的订单" }).click();
  await expect(page.getByTestId(`order-${orderNo!}`)).toBeVisible();
  return orderNo!;
}

async function chatRefund(page: Page, orderNo: string): Promise<void> {
  await page.getByTestId("chat-toggle").click();
  await page.getByTestId("chat-input").fill(`订单 ${orderNo} 我要退款`);
  await page.getByTestId("chat-send").click();
  await expect(page.getByText(/已提交退款申请/).first()).toBeVisible();
  await expect(page.getByText(/等待人工审核/).first()).toBeVisible();
}

async function adminLogin(
  page: Page,
  email: string,
  password: string,
): Promise<void> {
  await page.goto("/admin");
  const emailInput = page.locator("#admin-email");
  await page
    .locator('#admin-email, [data-testid="admin-session-info"]')
    .first()
    .waitFor({ state: "visible", timeout: 10_000 });
  let needsLogin = await emailInput
    .isVisible()
    .catch(() => false);
  if (!needsLogin) {
    const sessionInfo = await page
      .getByTestId("admin-session-info")
      .textContent();
    if (!sessionInfo?.includes(email)) {
      await page.getByTestId("admin-logout").click();
      await emailInput.waitFor({ state: "visible", timeout: 5_000 });
      needsLogin = true;
    }
  }
  if (needsLogin) {
    await emailInput.fill(email);
    await page.locator("#admin-password").fill(password);
    await page.getByTestId("admin-login").click();
  }
  await expect(
    page.getByRole("heading", { name: "售后运营台" }),
  ).toBeVisible();
  await page.getByTestId("tab-proposals").click();
}

test("purchase, chat refund proposal, admin approval, history restore", async ({
  page,
}) => {
  const phone = uniquePhone(1);
  await loginCustomer(page, phone);
  await addToCart(page);
  const orderNo = await checkout(page, phone, true);
  await chatRefund(page, orderNo);

  await page.reload();
  await page.getByTestId("chat-toggle").click();
  await expect(page.getByText(/已提交退款申请/).first()).toBeVisible();
  await page.goto("/after-sales");
  const proposalCard = page.locator('[data-testid^="after-sales-"]').first();
  await proposalCard
    .locator('[data-testid^="proposal-files-"]')
    .setInputFiles({
      name: "evidence.png",
      mimeType: "image/png",
      buffer: Buffer.from(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII=",
        "base64",
      ),
    });
  await proposalCard
    .locator('[data-testid^="attach-evidence-"]')
    .click();
  await expect(page.getByText(/已补充凭证/)).toBeVisible();

  await adminLogin(
    page,
    "owner@afterops.local",
    process.env.DEMO_OWNER_PASSWORD ?? "owner123456",
  );
  await page.getByTestId("tab-overview").click();
  await expect(page.getByTestId("overview-pending-proposals")).toBeVisible();
  await expect(page.getByText("近 7 天趋势")).toBeVisible();
  await page.getByTestId("overview-range").selectOption("168");
  await expect(
    page.getByRole("heading", { name: "近 7 天", exact: true }),
  ).toBeVisible();
  await page.getByTestId("overview-range").selectOption("720");
  await expect(
    page.getByRole("heading", { name: "近 30 天", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "近 30 天趋势", exact: true }),
  ).toBeVisible();
  await expect(page.getByTestId("export-trend")).toBeVisible();
  await expect(page.getByTestId("overview-audit-status")).toContainText(
    "哈希链完整",
  );
  await page.getByTestId("tab-finance").click();
  await expect(page.getByTestId("finance-paid-amount")).toBeVisible();
  await expect(page.getByTestId("finance-pending")).toBeVisible();
  await expect(page.getByTestId("finance-export")).toBeVisible();
  await page.getByTestId("tab-proposals").click();
  const card = page
    .locator(".proposal-card")
    .filter({ hasText: orderNo })
    .first();
  await expect(card).toBeVisible();
  await expect(card.locator(".attachment-grid img")).toHaveCount(1);
  await card.getByRole("button", { name: "批准并执行" }).click();
  await expect(page.getByText(/提案 #\d+ 已执行/)).toBeVisible();

  await page.getByRole("link", { name: "我的订单" }).click();
  const orderCard = page.getByTestId(`order-${orderNo}`);
  await expect(orderCard.getByText("已退款").first()).toBeVisible();

  await page.getByRole("link", { name: "售后进度" }).click();
  const afterSalesCard = page
    .locator('[data-testid^="after-sales-"]')
    .first();
  await expect(afterSalesCard).toContainText(orderNo);
  await expect(afterSalesCard).toContainText("已执行");
  await expect(page.getByTestId("notification-unread")).toHaveText("1");
  await page.getByTestId("notification-bell").click();
  await expect(page.getByText("退款已执行").first()).toBeVisible();

  await page.goto("/admin");
  await page.getByTestId("tab-audit").click();
  await expect(page.getByText("哈希链完整")).toBeVisible();
  await expect(page.getByTestId("audit-anchors")).toBeVisible();
  await page.getByTestId("audit-search").fill("NO-SUCH-ORDER");
  await page.getByTestId("audit-search").press("Enter");
  await expect(page.getByText("当前筛选下没有审计事件。")).toBeVisible();
  await page.getByTestId("audit-search").fill(orderNo);
  await page.getByTestId("audit-search").press("Enter");
  const auditCard = page
    .locator(".audit-card")
    .filter({ hasText: orderNo })
    .first();
  await expect(auditCard).toBeVisible();
  await auditCard.locator("summary").click();
  await expect(auditCard.locator(".audit-json")).toContainText(orderNo);

  const downloadPromise = page.waitForEvent("download");
  await page.getByTestId("audit-export").click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toContain("afterops-audit");
});

test("cancel an unpaid order", async ({ page }) => {
  const phone = uniquePhone(2);
  await loginCustomer(page, phone);
  await addToCart(page);
  const orderNo = await checkout(page, phone, false);

  const card = page.getByTestId(`order-${orderNo}`);
  await card.getByRole("button", { name: "取消订单" }).click();
  await expect(page.getByText(new RegExp(`订单 ${orderNo} 已取消`))).toBeVisible();
  await expect(card.getByText("已取消").first()).toBeVisible();
});

test("customer cancel request blocks shipment until rejected", async ({
  page,
}) => {
  const phone = uniquePhone(27);
  await loginCustomer(page, phone);
  await addToCart(page);
  const orderNo = await checkout(page, phone, true);

  const card = page.getByTestId(`order-${orderNo}`);
  await card.getByTestId(`request-cancel-${orderNo}`).click();
  await expect(
    page.getByText(new RegExp(`订单 ${orderNo} 取消申请已提交`)),
  ).toBeVisible();
  await expect(card).toContainText("取消申请待审核");

  await adminLogin(
    page,
    "owner@afterops.local",
    process.env.DEMO_OWNER_PASSWORD ?? "owner123456",
  );
  await page.getByTestId("tab-fulfillment").click();
  const fulfillment = page.getByTestId(`fulfillment-order-${orderNo}`);
  await fulfillment
    .getByTestId(`tracking-${orderNo}`)
    .fill(`SF-CANCEL-${Date.now()}`);
  await fulfillment.getByTestId(`create-shipment-${orderNo}`).click();
  await expect(page.getByText(/取消申请待审批/)).toBeVisible();

  await page.getByTestId("tab-proposals").click();
  await page.getByTestId("proposal-search").fill(orderNo);
  await page.getByTestId("proposal-search").press("Enter");
  const proposal = page
    .locator(".proposal-card")
    .filter({ hasText: orderNo })
    .first();
  await expect(proposal).toBeVisible();
  await proposal
    .locator("input[placeholder='驳回原因（可选）']")
    .fill("E2E 取消驳回");
  await proposal.getByRole("button", { name: "驳回" }).click();
  await expect(page.getByText(/提案 #\d+ 已驳回/)).toBeVisible();

  await page.getByTestId("tab-fulfillment").click();
  const retry = page.getByTestId(`fulfillment-order-${orderNo}`);
  await retry
    .getByTestId(`tracking-${orderNo}`)
    .fill(`SF-CANCEL-${Date.now()}`);
  await retry.getByTestId(`create-shipment-${orderNo}`).click();
  await expect(
    page.getByText(new RegExp(`订单 ${orderNo} 已创建运单`)),
  ).toBeVisible();
});

test("owner ships a paid order and customer sees the tracking timeline", async ({
  page,
}) => {
  const phone = uniquePhone(6);
  await loginCustomer(page, phone);
  await addToCart(page);
  const orderNo = await checkout(page, phone, true);
  const trackingNo = `SF${Date.now()}`;

  await adminLogin(
    page,
    "owner@afterops.local",
    process.env.DEMO_OWNER_PASSWORD ?? "owner123456",
  );
  await page.getByTestId("tab-fulfillment").click();
  const card = page.getByTestId(`fulfillment-order-${orderNo}`);
  await expect(card).toBeVisible();
  await card.getByTestId(`tracking-${orderNo}`).fill(trackingNo);
  await card.getByTestId(`create-shipment-${orderNo}`).click();
  await expect(
    page.getByText(new RegExp(`订单 ${orderNo} 已创建运单`)),
  ).toBeVisible();

  await page.getByTestId(`detail-${orderNo}`).click();
  await expect(page.getByTestId("admin-order-detail")).toContainText(orderNo);
  await expect(page.getByTestId("admin-order-detail")).toContainText("支付与退款");
  await page.getByTestId("admin-order-detail-close").click();

  await page.getByTestId(`advance-shipment-${orderNo}`).click();
  await expect(
    page.getByText(new RegExp(`订单 ${orderNo} 物流已更新为运输中`)),
  ).toBeVisible();

  await page.getByTestId(`node-${orderNo}`).fill("上海徐汇营业点");
  await page
    .getByTestId(`description-${orderNo}`)
    .fill("快递员正在派送");
  await page.getByTestId(`advance-shipment-${orderNo}`).click();
  await expect(
    page.getByText(new RegExp(`订单 ${orderNo} 物流已更新为派送中`)),
  ).toBeVisible();

  await page.getByTestId(`advance-shipment-${orderNo}`).click();
  await expect(
    page.getByText(new RegExp(`订单 ${orderNo} 物流已更新为已签收`)),
  ).toBeVisible();

  await page.getByRole("link", { name: "我的订单" }).click();
  const orderCard = page.getByTestId(`order-${orderNo}`);
  await expect(orderCard.getByText("已签收").first()).toBeVisible();
  await orderCard.getByRole("button", { name: "查看详情" }).click();
  const timeline = page.getByTestId(`shipment-timeline-${orderNo}`);
  await expect(timeline).toBeVisible();
  await expect(timeline).toContainText("上海徐汇营业点");
  await expect(timeline).toContainText("已签收");
  await expect(orderCard).toContainText(trackingNo);

  await orderCard.getByRole("button", { name: "确认收货" }).click();
  await expect(page.getByText(new RegExp(`订单 ${orderNo} 已确认收货`))).toBeVisible();
  await expect(orderCard.getByText("已完成").first()).toBeVisible();

  const reviewRow = page
    .locator(`[data-testid^="review-row-${orderNo}-"]`)
    .first();
  await expect(reviewRow).toBeVisible();
  const rowTestId = await reviewRow.getAttribute("data-testid");
  const sku = rowTestId?.replace(`review-row-${orderNo}-`, "");
  expect(sku).toBeTruthy();
  await reviewRow
    .locator('select[data-testid^="review-rating-"]')
    .selectOption("5");
  const reviewContent = `降噪好-${Date.now()}`;
  await reviewRow
    .locator('input[data-testid^="review-content-"]')
    .fill(reviewContent);
  await reviewRow
    .locator('input[data-testid^="review-files-"]')
    .setInputFiles({
      name: "review.png",
      mimeType: "image/png",
      buffer: PNG,
    });
  await reviewRow
    .locator('button[data-testid^="submit-review-"]')
    .click();
  await expect(page.getByText("已提交 5 星评价")).toBeVisible();
  await expect(reviewRow).toContainText("已评价 5 星");
  const followUpContent = `追评-${Date.now()}`;
  await reviewRow
    .locator(`input[data-testid="review-follow-up-${orderNo}-${sku}"]`)
    .fill(followUpContent);
  await reviewRow
    .locator(
      `input[data-testid="review-follow-up-files-${orderNo}-${sku}"]`,
    )
    .setInputFiles({
      name: "follow-up.png",
      mimeType: "image/png",
      buffer: PNG,
    });
  await reviewRow
    .locator(
      `button[data-testid="submit-review-follow-up-${orderNo}-${sku}"]`,
    )
    .click();
  await expect(page.getByText("追评已提交")).toBeVisible();
  await expect(
    reviewRow.getByTestId(`review-follow-up-view-${orderNo}-${sku}`),
  ).toContainText(followUpContent);
  await expect(reviewRow.locator(".review-image")).toHaveCount(2);

  await page.goto(`/product/${sku}`);
  await expect(page.getByTestId("product-reviews")).toContainText(
    reviewContent,
  );
  await expect(page.getByTestId("product-reviews")).toContainText(
    followUpContent,
  );
  await expect(
    page.getByTestId("product-reviews").locator(".review-image"),
  ).toHaveCount(2);

  await page.goto("/admin");
  await page.getByTestId("tab-product-reviews").click();
  const reviewCard = page
    .locator(".proposal-card")
    .filter({ hasText: reviewContent })
    .first();
  await expect(reviewCard).toBeVisible();
  await expect(reviewCard.locator(".review-image")).toHaveCount(2);
  await expect(reviewCard).toContainText(followUpContent);
  const adminReviewId = (
    await reviewCard.getAttribute("data-testid")
  )?.replace("admin-review-", "");
  expect(adminReviewId).toBeTruthy();
  await reviewCard
    .getByTestId(`review-reply-${adminReviewId}`)
    .fill("感谢反馈，我们会改进包装。");
  await reviewCard
    .getByTestId(`save-review-reply-${adminReviewId}`)
    .click();
  await expect(page.getByText(/回复已保存/)).toBeVisible();
  await expect(reviewCard.locator(".merchant-reply")).toContainText(
    "感谢反馈",
  );

  await page.goto(`/product/${sku}`);
  await expect(page.getByTestId("product-reviews")).toContainText(
    "感谢反馈，我们会改进包装。",
  );

  await page.goto("/admin");
  await page.getByTestId("tab-product-reviews").click();
  const cardToHide = page
    .locator(".proposal-card")
    .filter({ hasText: reviewContent })
    .first();
  await expect(cardToHide).toBeVisible();
  await cardToHide.getByRole("button", { name: "隐藏评价" }).click();
  await expect(page.getByText(/评价 #\d+ 已隐藏/)).toBeVisible();

  await page.goto(`/product/${sku}`);
  await expect(page.getByTestId("product-reviews")).not.toContainText(
    reviewContent,
  );
});

test("human handoff creates a real ticket and agent resolves it", async ({
  page,
}) => {
  const phone = uniquePhone(7);
  await loginCustomer(page, phone);
  await addToCart(page);
  const contextOrderNo = await checkout(page, phone, false);
  await page.getByTestId("chat-toggle").click();
  await page.getByTestId("chat-input").fill("转人工");
  await page.getByTestId("chat-send").click();
  await expect(page.getByText(/已记录转人工请求/).first()).toBeVisible();
  const ticketChip = page.getByTestId("chat-handoff-ticket");
  await expect(ticketChip).toBeVisible();
  const ticketText = await ticketChip.textContent();
  const ticketRef = ticketText?.match(/TK-\d{6}/)?.[0];
  expect(ticketRef).toBeTruthy();

  await adminLogin(
    page,
    "agent@afterops.local",
    process.env.DEMO_AGENT_PASSWORD ?? "agent123456",
  );
  await page.getByTestId("tab-handoffs").click();
  await page.getByTestId("handoff-search").fill(ticketRef!);
  await page.getByTestId("handoff-search").press("Enter");
  const card = page.locator(".handoff-card").filter({ hasText: ticketRef! }).first();
  await expect(card).toBeVisible();
  const summaryPanel = card.locator(".handoff-summary");
  await expect(summaryPanel).toContainText("【诉求】");
  await expect(summaryPanel).toContainText("【建议】");
  await expect(summaryPanel).toContainText("【会话】");
  await card.getByRole("button", { name: "刷新摘要" }).click();
  await expect(
    page.getByText(new RegExp(`工单 ${ticketRef} 会话摘要已更新`)),
  ).toBeVisible();
  await card.getByRole("button", { name: "接管工单" }).click();
  await expect(page.getByText(new RegExp(`工单 ${ticketRef} 已接管`))).toBeVisible();
  await card.getByRole("button", { name: "查看会话" }).click();
  const handoffContext = card.getByTestId("handoff-context");
  await expect(handoffContext).toContainText("历史订单 1");
  await expect(handoffContext).toContainText(contextOrderNo);
  await card
    .getByPlaceholder("内部备注（1000 字以内）")
    .fill("内部备注：优先回访");
  await card.getByRole("button", { name: "保存备注" }).click();
  await expect(
    page.getByText(new RegExp(`工单 ${ticketRef} 内部备注已保存`)),
  ).toBeVisible();
  await expect(
    card.locator(".handoff-note").filter({ hasText: "优先回访" }),
  ).toBeVisible();
  await card
    .getByPlaceholder("回复顾客（1000 字以内）")
    .fill("人工客服已收到，正在核实。");
  await card.getByRole("button", { name: "发送回复" }).click();
  await expect(
    page.getByText(new RegExp(`已回复工单 ${ticketRef}`)),
  ).toBeVisible();
  await expect(
    card.locator(".handoff-msg.agent").filter({ hasText: "人工客服已收到" }),
  ).toBeVisible();
  await card.getByPlaceholder("处理说明（可选）").fill("E2E 客服已回访");
  await card.getByRole("button", { name: "标记解决" }).click();
  await expect(
    page.getByText(new RegExp(`工单 ${ticketRef} 已标记解决`)),
  ).toBeVisible();

  await page.goto("/");
  await page.getByTestId("chat-toggle").click();
  await expect(
    page.getByText("人工客服已收到，正在核实。").first(),
  ).toBeVisible();
  await page.getByTestId("chat-toggle").click();

  await page.getByRole("link", { name: "售后进度" }).click();
  const customerTicket = page
    .locator('[data-testid^="customer-handoff-"]')
    .filter({ hasText: ticketRef! })
    .first();
  await expect(customerTicket).toContainText("已解决");
  await customerTicket
    .locator('[data-testid^="rating-score-"]')
    .selectOption("5");
  await customerTicket
    .locator('[data-testid^="rating-comment-"]')
    .fill("客服处理很快");
  await customerTicket
    .locator('[data-testid^="submit-rating-"]')
    .click();
  await expect(page.getByText(/评价已提交：5 星/)).toBeVisible();
  await expect(customerTicket).toContainText("已评价 5 星");
  await expect(page.getByTestId("notification-unread")).toHaveText("2");
  await page.getByTestId("notification-bell").click();
  await expect(page.getByText("客服回复了工单").first()).toBeVisible();
  await expect(page.getByText("人工工单已解决").first()).toBeVisible();
});

test("customer chat receives an agent reply in real time", async ({
  page,
}) => {
  const phone = uniquePhone(24);
  await loginCustomer(page, phone);
  await page.getByTestId("chat-toggle").click();
  await page.getByTestId("chat-input").fill("转人工");
  await page.getByTestId("chat-send").click();
  const ticketChip = page.getByTestId("chat-handoff-ticket");
  await expect(ticketChip).toBeVisible();
  await expect(page.getByTestId("chat-live")).toBeVisible({
    timeout: 5000,
  });
  const ticketRef = (await ticketChip.textContent())?.match(/TK-\d{6}/)?.[0];
  expect(ticketRef).toBeTruthy();
  const ticketId = Number(ticketRef!.slice(3));

  const adminLoginResponse = await page.request.post(
    "http://127.0.0.1:3000/api/admin/session",
    {
      data: {
        email: "owner@afterops.local",
        password: process.env.DEMO_OWNER_PASSWORD ?? "owner123456",
      },
    },
  );
  expect(adminLoginResponse.ok()).toBeTruthy();
  const cookies = await page.context().cookies();
  const adminCsrf = cookies.find(
    (cookie) => cookie.name === "afterops_admin_csrf",
  )?.value;
  const customerCsrf = cookies.find(
    (cookie) => cookie.name === "afterops_csrf",
  )?.value;
  expect(adminCsrf).toBeTruthy();
  expect(customerCsrf).toBeTruthy();
  const reply = await page.request.post(
    `http://127.0.0.1:3000/api/admin/handoffs/${ticketId}/messages`,
    {
      headers: {
        "X-Admin-CSRF-Token": adminCsrf!,
        "X-CSRF-Token": customerCsrf!,
      },
      data: { content: "轮询测试：客服已回复" },
    },
  );
  const replyBody = reply.ok() ? "" : await reply.text();
  expect(reply.ok(), `${reply.status()} ${replyBody}`).toBeTruthy();
  await expect(
    page.getByText("轮询测试：客服已回复").first(),
  ).toBeVisible({ timeout: 4_000 });
  await expect(
    page.locator(".chat-author").filter({ hasText: "客服" }).first(),
  ).toBeVisible();
  await expect(page.getByTestId("notification-unread")).toHaveText("1", {
    timeout: 5_000,
  });
  await page.getByTestId("notification-bell").click();
  await expect(
    page.getByText("客服回复了工单").first(),
  ).toBeVisible();
});

test("low CSAT creates a high priority follow-up ticket", async ({
  page,
}) => {
  const phone = uniquePhone(34);
  await loginCustomer(page, phone);
  await page.getByTestId("chat-toggle").click();
  await page.getByTestId("chat-input").fill("转人工");
  await page.getByTestId("chat-send").click();
  const ticketChip = page.getByTestId("chat-handoff-ticket");
  await expect(ticketChip).toBeVisible();
  const ticketRef = (await ticketChip.textContent())?.match(/TK-\d{6}/)?.[0];
  expect(ticketRef).toBeTruthy();
  const ticketId = Number(ticketRef!.slice(3));

  const adminLoginResponse = await page.request.post(
    "http://127.0.0.1:3000/api/admin/session",
    {
      data: {
        email: "owner@afterops.local",
        password: process.env.DEMO_OWNER_PASSWORD ?? "owner123456",
      },
    },
  );
  expect(adminLoginResponse.ok()).toBeTruthy();
  const cookies = await page.context().cookies();
  const adminCsrf = cookies.find(
    (cookie) => cookie.name === "afterops_admin_csrf",
  )?.value;
  const customerCsrf = cookies.find(
    (cookie) => cookie.name === "afterops_csrf",
  )?.value;
  const headers = {
    "X-Admin-CSRF-Token": adminCsrf!,
    "X-CSRF-Token": customerCsrf!,
  };
  expect(
    (
      await page.request.post(
        `http://127.0.0.1:3000/api/admin/handoffs/${ticketId}/claim`,
        { headers },
      )
    ).ok(),
  ).toBeTruthy();
  expect(
    (
      await page.request.post(
        `http://127.0.0.1:3000/api/admin/handoffs/${ticketId}/resolve`,
        { headers, data: { note: "首次处理完成" } },
      )
    ).ok(),
  ).toBeTruthy();

  await page.goto("/after-sales");
  const original = page
    .locator('[data-testid^="customer-handoff-"]')
    .filter({ hasText: ticketRef! })
    .first();
  await original.locator('[data-testid^="rating-score-"]').selectOption("2");
  await original
    .locator('[data-testid^="rating-comment-"]')
    .fill("等待太久，体验不好");
  await original.locator('[data-testid^="submit-rating-"]').click();
  await expect(page.getByText(/已安排回访工单 TK-\d{6}/)).toBeVisible();
  const followUpRef = (
    await page.getByText(/已安排回访工单 TK-\d{6}/).textContent()
  )?.match(/TK-\d{6}/)?.[0];
  expect(followUpRef).toBeTruthy();
  const followUpId = Number(followUpRef!.slice(3));
  await expect(
    page.getByTestId(`customer-handoff-${followUpId}`),
  ).toContainText("低分回访");

  await adminLogin(
    page,
    "owner@afterops.local",
    process.env.DEMO_OWNER_PASSWORD ?? "owner123456",
  );
  await page.getByTestId("tab-handoffs").click();
  await page.getByTestId("handoff-search").fill(followUpRef!);
  await page.getByTestId("handoff-search").press("Enter");
  const followUpCard = page
    .locator(".handoff-card")
    .filter({ hasText: followUpRef! })
    .first();
  await expect(followUpCard).toContainText("低分回访");
  await followUpCard.getByRole("button", { name: "接管工单" }).click();
  await expect(
    page.getByText(new RegExp(`工单 ${followUpRef} 已接管`)),
  ).toBeVisible();
  await followUpCard.getByRole("button", { name: "查看会话" }).click();
  await followUpCard
    .getByPlaceholder("回复顾客（1000 字以内）")
    .fill("抱歉让您久等了，我们会跟进处理。");
  await followUpCard.getByRole("button", { name: "发送回复" }).click();
  await expect(
    page.getByText(new RegExp(`已回复工单 ${followUpRef}`)),
  ).toBeVisible();

  await page.goto("/after-sales");
  await expect(
    page.getByTestId(`customer-handoff-${followUpId}`),
  ).toContainText("我们会跟进处理");
});

test("customer sends evidence images and agent sees them in handoff", async ({
  page,
}) => {
  const phone = uniquePhone(25);
  await loginCustomer(page, phone);
  await page.getByTestId("chat-toggle").click();
  await page.getByTestId("chat-image-input").setInputFiles({
    name: "evidence.png",
    mimeType: "image/png",
    buffer: PNG,
  });
  await expect(page.getByTestId("chat-attachments")).toBeVisible();
  await page.getByTestId("chat-input").fill("耳机外壳有裂纹");
  await page.getByTestId("chat-send").click();
  await expect(page.getByTestId("chat-image").first()).toBeVisible();
  await expect(page.getByText(/无法识别图片/).first()).toBeVisible();

  await page.getByTestId("chat-input").fill("转人工");
  await page.getByTestId("chat-send").click();
  const ticketChip = page.getByTestId("chat-handoff-ticket");
  await expect(ticketChip).toBeVisible();
  const ticketRef = (await ticketChip.textContent())?.match(/TK-\d{6}/)?.[0];
  expect(ticketRef).toBeTruthy();

  await adminLogin(
    page,
    "owner@afterops.local",
    process.env.DEMO_OWNER_PASSWORD ?? "owner123456",
  );
  await page.getByTestId("tab-handoffs").click();
  await page.getByTestId("handoff-search").fill(ticketRef!);
  await page.getByTestId("handoff-search").press("Enter");
  const card = page.locator(".handoff-card").filter({ hasText: ticketRef! }).first();
  await expect(card).toBeVisible();
  await card.getByRole("button", { name: "查看会话" }).click();
  const images = card.locator('[data-testid^="handoff-images-"]').first();
  await expect(images).toBeVisible();
  await expect(images.locator("img")).toHaveAttribute(
    "src",
    /\/api\/admin\/attachments\/\d+/,
  );

  const ticketId = Number(ticketRef!.slice(3));
  await card
    .getByTestId(`handoff-reply-files-${ticketId}`)
    .setInputFiles({
      name: "reply.png",
      mimeType: "image/png",
      buffer: PNG,
    });
  await expect(
    card.getByTestId(`handoff-reply-attachments-${ticketId}`),
  ).toBeVisible();
  await card
    .getByTestId(`handoff-reply-${ticketId}`)
    .fill("请按图示包装后寄回");
  await card.getByTestId(`handoff-send-${ticketId}`).click();
  await expect(
    page.getByText(new RegExp(`已回复工单 ${ticketRef}`)),
  ).toBeVisible();
  const agentImages = card
    .locator(".handoff-msg.agent")
    .locator('[data-testid^="handoff-images-"]')
    .last();
  await expect(agentImages).toBeVisible();
  await expect(agentImages.locator("img")).toHaveAttribute(
    "src",
    /\/api\/admin\/attachments\/\d+/,
  );

  await page.goto("/");
  await page.getByTestId("chat-toggle").click();
  const agentMessage = page
    .locator(".chat-message.agent")
    .filter({ hasText: "请按图示包装后寄回" })
    .first();
  await expect(agentMessage).toBeVisible();
  await expect(agentMessage.getByTestId("chat-image")).toBeVisible();
});

test("owner manages quick replies and agent uses one", async ({ page }) => {
  const phone = uniquePhone(26);
  await loginCustomer(page, phone);
  await page.getByTestId("chat-toggle").click();
  await page.getByTestId("chat-input").fill("转人工");
  await page.getByTestId("chat-send").click();
  const ticketChip = page.getByTestId("chat-handoff-ticket");
  await expect(ticketChip).toBeVisible();
  const ticketRef = (await ticketChip.textContent())?.match(/TK-\d{6}/)?.[0];
  expect(ticketRef).toBeTruthy();

  const title = `E2E话术-${Date.now()}`;
  const content = "您好，物流信息已为您核实，请稍等。";
  await adminLogin(
    page,
    "owner@afterops.local",
    process.env.DEMO_OWNER_PASSWORD ?? "owner123456",
  );
  await page.getByTestId("tab-quick-replies").click();
  await page.getByTestId("quick-reply-title").fill(title);
  await page.getByTestId("quick-reply-category").fill("物流");
  await page.getByTestId("quick-reply-content").fill(content);
  await page.getByTestId("create-quick-reply").click();
  await expect(
    page.getByText(new RegExp(`话术「${title}」已创建`)),
  ).toBeVisible();

  await adminLogin(
    page,
    "agent@afterops.local",
    process.env.DEMO_AGENT_PASSWORD ?? "agent123456",
  );
  await page.getByTestId("tab-handoffs").click();
  await page.getByTestId("handoff-search").fill(ticketRef!);
  await page.getByTestId("handoff-search").press("Enter");
  const card = page
    .locator(".handoff-card")
    .filter({ hasText: ticketRef! })
    .first();
  await expect(card).toBeVisible();
  await card.getByRole("button", { name: "接管工单" }).click();
  await expect(
    page.getByText(new RegExp(`工单 ${ticketRef} 已接管`)),
  ).toBeVisible();
  await card.getByRole("button", { name: "查看会话" }).click();
  await card
    .getByLabel("套用话术模板")
    .selectOption({ label: `物流 · ${title}` });
  await expect(card.getByPlaceholder("回复顾客（1000 字以内）")).toHaveValue(
    content,
  );
  await card.getByRole("button", { name: "发送回复" }).click();
  await expect(
    page.getByText(new RegExp(`已回复工单 ${ticketRef}`)),
  ).toBeVisible();
  await expect(
    card.locator(".handoff-msg.agent").filter({ hasText: content }),
  ).toBeVisible();
});

test("owner assigns a handoff ticket to an agent", async ({ page }) => {
  const phone = uniquePhone(15);
  await loginCustomer(page, phone);
  await page.getByTestId("chat-toggle").click();
  await page.getByTestId("chat-input").fill("转人工");
  await page.getByTestId("chat-send").click();
  const ticketChip = page.getByTestId("chat-handoff-ticket");
  await expect(ticketChip).toBeVisible();
  const ticketText = await ticketChip.textContent();
  const ticketRef = ticketText?.match(/TK-\d{6}/)?.[0];
  expect(ticketRef).toBeTruthy();

  await adminLogin(
    page,
    "owner@afterops.local",
    process.env.DEMO_OWNER_PASSWORD ?? "owner123456",
  );
  await page.getByTestId("tab-handoffs").click();
  await page.getByTestId("handoff-search").fill(ticketRef!);
  await page.getByTestId("handoff-search").press("Enter");
  const card = page.locator(".handoff-card").filter({ hasText: ticketRef! }).first();
  await expect(card).toBeVisible();
  await card
    .locator('select[aria-label$="指派客服"]')
    .selectOption({ label: "agent@afterops.local" });
  await card.getByRole("button", { name: "指派", exact: true }).click();
  await expect(
    page.getByText(new RegExp(`工单 ${ticketRef} 已指派`)),
  ).toBeVisible();
  await expect(card).toContainText("agent@afterops.local");
});

test("customer withdraws a pending after-sales request", async ({ page }) => {
  const phone = uniquePhone(8);
  await loginCustomer(page, phone);
  await addToCart(page);
  const orderNo = await checkout(page, phone, true);
  await chatRefund(page, orderNo);
  await page.getByRole("link", { name: "售后进度" }).click();
  const card = page.locator('[data-testid^="after-sales-"]').first();
  await expect(card).toBeVisible();
  await card.getByRole("button", { name: "撤回申请" }).click();
  await expect(page.getByText(/售后申请 #\d+ 已撤回/)).toBeVisible();
  await expect(
    page.locator('[data-testid^="after-sales-"]').first(),
  ).toContainText("已撤回");
});

test("owner rejects a refund proposal without moving money", async ({ page }) => {
  const phone = uniquePhone(3);
  await loginCustomer(page, phone);
  await addToCart(page);
  const orderNo = await checkout(page, phone, true);
  await chatRefund(page, orderNo);

  await adminLogin(
    page,
    "owner@afterops.local",
    process.env.DEMO_OWNER_PASSWORD ?? "owner123456",
  );
  await page.getByTestId("tab-overview").click();
  await expect(page.getByTestId("overview-pending-proposals")).toBeVisible();
  await expect(page.getByText("近 7 天趋势")).toBeVisible();
  await expect(page.getByTestId("overview-audit-status")).toContainText(
    "哈希链完整",
  );
  await page.getByTestId("tab-proposals").click();
  const card = page
    .locator(".proposal-card")
    .filter({ hasText: orderNo })
    .first();
  await expect(card).toBeVisible();
  await card.locator("input[placeholder='驳回原因（可选）']").fill("E2E 凭证不足");
  await card.getByRole("button", { name: "驳回" }).click();
  await expect(page.getByText(/提案 #\d+ 已驳回/)).toBeVisible();

  await page.getByRole("link", { name: "我的订单" }).click();
  const orderCard = page.getByTestId(`order-${orderNo}`);
  await expect(orderCard.getByText("已支付").first()).toBeVisible();
});

test("complaint handoff is urgent in the admin queue", async ({ page }) => {
  const phone = uniquePhone(13);
  await loginCustomer(page, phone);
  await page.getByTestId("chat-toggle").click();
  await page.getByTestId("chat-input").fill("我要投诉，物流太慢");
  await page.getByTestId("chat-send").click();
  const ticketChip = page.getByTestId("chat-handoff-ticket");
  await expect(ticketChip).toBeVisible();
  const ticketText = await ticketChip.textContent();
  const ticketRef = ticketText?.match(/TK-\d{6}/)?.[0];
  expect(ticketRef).toBeTruthy();

  await adminLogin(
    page,
    "agent@afterops.local",
    process.env.DEMO_AGENT_PASSWORD ?? "agent123456",
  );
  await page.getByTestId("tab-handoffs").click();
  await page.getByTestId("handoff-search").fill(ticketRef!);
  await page.getByTestId("handoff-search").press("Enter");
  const card = page.locator(".handoff-card").filter({ hasText: ticketRef! }).first();
  await expect(card).toContainText("紧急");
  await page.getByTestId("handoff-priority-filter").selectOption("urgent");
  await expect(
    page.locator(".handoff-card").filter({ hasText: ticketRef! }).first(),
  ).toBeVisible();
});

test("admin searches customer directory by phone", async ({ page }) => {
  const phone = uniquePhone(14);
  await loginCustomer(page, phone);
  await addToCart(page);
  const orderNo = await checkout(page, phone, true);

  await adminLogin(
    page,
    "owner@afterops.local",
    process.env.DEMO_OWNER_PASSWORD ?? "owner123456",
  );
  await page.getByTestId("tab-customers").click();
  await page.getByTestId("customer-search").fill(phone);
  await page.getByRole("button", { name: "搜索" }).click();

  const card = page.locator(".proposal-card").filter({ hasText: phone }).first();
  await expect(card).toBeVisible();
  await card.getByRole("button", { name: "查看档案" }).click();
  await expect(page.getByTestId("customer-detail")).toBeVisible();
  await expect(
    page.getByTestId(`customer-order-${orderNo}`),
  ).toBeVisible();
});

test("owner creates and approves a manual refund proposal", async ({ page }) => {
  const phone = uniquePhone(12);
  await loginCustomer(page, phone);
  await addToCart(page);
  const orderNo = await checkout(page, phone, true);

  await adminLogin(
    page,
    "owner@afterops.local",
    process.env.DEMO_OWNER_PASSWORD ?? "owner123456",
  );
  await page.getByText("代顾客发起售后动作").click();
  await page.getByTestId("manual-action-order").fill(orderNo);
  await page.getByTestId("manual-action-amount").fill("1");
  await page.getByTestId("manual-action-reason").fill("E2E 电话协商部分退款");
  await page.getByTestId("manual-action-submit").click();
  await expect(page.getByText(/退款提案 #\d+ 已创建/)).toBeVisible();

  const card = page
    .locator(".proposal-card")
    .filter({ hasText: orderNo })
    .first();
  await expect(card).toContainText("店主发起");
  await expect(card).toContainText("1.00");
  await card.getByRole("button", { name: "批准并执行" }).click();
  await expect(page.getByText(/提案 #\d+ 已执行/)).toBeVisible();

  await page.getByTestId("tab-finance").click();
  await expect(
    page.getByTestId("refund-auto-settle-hint"),
  ).toContainText("自动标记渠道到账");
  await expect(
    page.getByTestId("refund-auto-settle-hint"),
  ).toContainText("自动重试");
  const settlementQueue = page.getByTestId("refund-settlement-queue");
  const refundCard = settlementQueue
    .locator(".proposal-card")
    .filter({ hasText: orderNo })
    .first();
  await expect(refundCard).toContainText("渠道处理中");
  const settlementReference = `PSP-E2E-${Date.now()}`;
  await refundCard
    .locator('[data-testid^="refund-settlement-reference-"]')
    .fill(settlementReference);
  await refundCard
    .locator('[data-testid^="refund-settlement-success-"]')
    .click();
  await expect(
    page.getByText(/退款 #\d+ 已标记渠道到账/),
  ).toBeVisible();
  await expect(refundCard).toContainText("渠道已到账");
  await expect(refundCard).toContainText(settlementReference);

  await page.goto("/orders");
  const settledOrder = page.getByTestId(`order-${orderNo}`);
  await settledOrder.getByRole("button", { name: "查看详情" }).click();
  const settlementLedger = page.getByTestId(`refund-ledger-${orderNo}`);
  await expect(settlementLedger).toContainText("渠道已到账");
  await expect(settlementLedger).toContainText(settlementReference);

  await page.getByRole("link", { name: "我的订单" }).click();
  await expect(page.getByTestId(`order-${orderNo}`)).toContainText("部分退款");
});

test("agent can view proposals but cannot approve", async ({ page }) => {
  const phone = uniquePhone(4);
  await loginCustomer(page, phone);
  await addToCart(page);
  const orderNo = await checkout(page, phone, true);
  await chatRefund(page, orderNo);

  await adminLogin(
    page,
    "agent@afterops.local",
    process.env.DEMO_AGENT_PASSWORD ?? "agent123456",
  );
  await expect(page.getByText(/可以接管人工工单/)).toBeVisible();
  const card = page
    .locator(".proposal-card")
    .filter({ hasText: orderNo })
    .first();
  await expect(card).toBeVisible();
  await expect(
    card.getByRole("button", { name: "批准并执行" }),
  ).toHaveCount(0);

  await page.getByTestId("tab-fulfillment").click();
  await expect(
    page.getByText(/客服账号只能查看履约状态/),
  ).toBeVisible();
  const fulfillmentCard = page.getByTestId(`fulfillment-order-${orderNo}`);
  await expect(fulfillmentCard).toBeVisible();
  await expect(
    fulfillmentCard.getByTestId(`create-shipment-${orderNo}`),
  ).toHaveCount(0);

  await page.getByTestId("tab-products").click();
  await expect(page.getByText(/只能查看商品与库存/)).toBeVisible();
  await expect(page.getByTestId("tab-staff")).toHaveCount(0);
});

test("return request flows from delivery to return tracking and refund", async ({
  page,
}) => {
  const phone = uniquePhone(9);
  await loginCustomer(page, phone);
  await addToCart(page);
  const orderNo = await checkout(page, phone, true);
  const returnTracking = `SF-RET-${Date.now()}`;

  await adminLogin(
    page,
    "owner@afterops.local",
    process.env.DEMO_OWNER_PASSWORD ?? "owner123456",
  );
  await page.getByTestId("tab-returns").click();
  await page.getByTestId("return-policy-window").fill("7");
  await page.getByTestId("return-policy-fee").fill("0");
  await page.getByTestId("return-policy-categories").fill("");
  await page.getByTestId("return-policy-windows").fill("");
  await page.getByTestId("save-return-policy").click();
  await expect(page.getByText("退货政策已保存")).toBeVisible();
  await page.getByTestId("tab-fulfillment").click();
  const fulfillmentCard = page.getByTestId(`fulfillment-order-${orderNo}`);
  await fulfillmentCard.getByTestId(`tracking-${orderNo}`).fill(`SF-OUT-${Date.now()}`);
  await fulfillmentCard.getByTestId(`create-shipment-${orderNo}`).click();
  await expect(
    page.getByText(new RegExp(`订单 ${orderNo} 已创建运单`)),
  ).toBeVisible();
  await page.getByTestId(`advance-shipment-${orderNo}`).click();
  await expect(
    page.getByText(new RegExp(`订单 ${orderNo} 物流已更新为运输中`)),
  ).toBeVisible();
  await page.getByTestId(`advance-shipment-${orderNo}`).click();
  await expect(
    page.getByText(new RegExp(`订单 ${orderNo} 物流已更新为派送中`)),
  ).toBeVisible();
  await page.getByTestId(`advance-shipment-${orderNo}`).click();
  await expect(
    page.getByText(new RegExp(`订单 ${orderNo} 物流已更新为已签收`)),
  ).toBeVisible();

  await page.goto("/after-sales");
  await page.getByTestId("return-files").setInputFiles({
    name: "evidence.png",
    mimeType: "image/png",
    buffer: Buffer.from(
      "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII=",
      "base64",
    ),
  });
  await page.getByTestId("return-resolution").selectOption("exchange");
  await expect(page.getByTestId("exchange-sku")).toBeVisible();
  await page.getByTestId("return-resolution").selectOption("refund");
  await page.getByTestId("return-reason-category").selectOption("quality");
  await page.getByTestId("return-reason").fill("耳机有杂音，需要退货");
  await page.getByTestId("create-return").click();
  await expect(page.getByText("退货申请已提交，等待店主审核")).toBeVisible();

  await page.goto("/admin");
  await page.getByTestId("tab-returns").click();
  await page.getByTestId("return-analytics").locator("summary").click();
  await expect(
    page.getByTestId("return-analytics-reason-quality"),
  ).toContainText("质量问题");
  const adminReturn = page.locator('[data-testid^="admin-return-"]').first();
  await expect(adminReturn).toContainText(orderNo);
  await expect(adminReturn).toContainText("质量问题");
  const evidence = adminReturn.locator(".attachment-grid img").first();
  await expect(evidence).toBeVisible();
  await expect
    .poll(() => evidence.evaluate((image) => (image as HTMLImageElement).naturalWidth))
    .toBeGreaterThan(0);
  await adminReturn.getByRole("button", { name: "批准退货" }).click();
  await expect(page.getByText(/退货 #\d+ 已批准/)).toBeVisible();

  await page.goto("/after-sales");
  const customerReturn = page.locator('article[data-testid^="return-"]').first();
  await customerReturn.getByPlaceholder("承运商").fill("顺丰速运");
  await customerReturn.getByPlaceholder("退货运单号").fill(returnTracking);
  await customerReturn.getByRole("button", { name: "登记回寄" }).click();
  await expect(page.getByText(/已登记回寄单号/)).toBeVisible();

  await page.goto("/admin");
  await page.getByTestId("tab-returns").click();
  const readyReturn = page.locator('[data-testid^="admin-return-"]').first();
  await expect(
    readyReturn.locator('select[data-testid^="disposition-"]').first(),
  ).toHaveValue("restock");
  await readyReturn
    .locator('select[data-testid^="responsibility-"]')
    .first()
    .selectOption("customer");
  await readyReturn
    .locator('input[data-testid^="item-deduction-"]')
    .first()
    .fill("1");
  await readyReturn
    .locator('input[data-testid^="inspection-note-"]')
    .fill("缺少原装耳塞套，折价 1 元");
  await readyReturn.getByRole("button", { name: "确认收货并生成退款提案" }).click();
  await expect(
    page.getByText(/已收货.*回补库存 1 件.*退款提案 #\d+ 待审批/),
  ).toBeVisible();
  await page.getByTestId("return-filter").selectOption("received");
  const receivedReturn = page
    .locator('[data-testid^="admin-return-"]')
    .filter({ hasText: orderNo })
    .first();
  await expect(receivedReturn).toContainText("质检扣减 1.00 元");
  await expect(receivedReturn).toContainText("缺少原装耳塞套");
  await expect(receivedReturn).toContainText("顾客责任");
  const inspectionReport = await page.request.get(
    "http://127.0.0.1:3000/api/admin/returns/inspection-report.csv",
  );
  expect(inspectionReport.ok()).toBeTruthy();
  const reportText = await inspectionReport.text();
  expect(reportText).toContain("责任判定");
  expect(reportText).toContain(orderNo);
  expect(reportText).toContain("顾客责任");

  await page.getByTestId("tab-proposals").click();
  const proposalCard = page
    .locator(".proposal-card")
    .filter({ hasText: orderNo })
    .first();
  await expect(proposalCard).toBeVisible();
  await expect(proposalCard).toContainText("质检扣减 1.00 元");
  await proposalCard.getByRole("button", { name: "批准并执行" }).click();
  await expect(page.getByText(/提案 #\d+ 已执行/)).toBeVisible();

  await page.getByRole("link", { name: "我的订单" }).click();
  await expect(page.getByTestId(`order-${orderNo}`)).toContainText("部分退款");

  await page.goto("/after-sales");
  const timeline = page.getByTestId("customer-after-sales-timeline");
  await expect(timeline).toBeVisible();
  await expect(timeline).toContainText("退货申请已通过");
  await expect(timeline).toContainText("退货物流 已登记回寄");
  await expect(timeline).toContainText("退款已执行");
  await expect(timeline).toContainText(orderNo);
  const refundedReturn = page
    .locator('article[data-testid^="return-"]')
    .filter({ hasText: orderNo })
    .first();
  await expect(refundedReturn).toContainText("质检扣减 1.00 元");
  await expect(refundedReturn).toContainText("缺少原装耳塞套");
  await expect(refundedReturn).toContainText("顾客责任");

  await page.goto("/admin");
  await page.getByTestId("tab-customers").click();
  await page.getByTestId("customer-search").fill(phone);
  await page.getByRole("button", { name: "搜索" }).click();
  const customerCard = page
    .locator(".proposal-card")
    .filter({ hasText: phone })
    .first();
  await customerCard.getByRole("button", { name: "查看档案" }).click();
  const adminTimeline = page.getByTestId("admin-customer-timeline");
  await expect(adminTimeline).toContainText("退货申请已通过");
  await expect(adminTimeline).toContainText("退款已执行");
});

test("customer appeals a return deduction and owner accepts it", async ({
  page,
}) => {
  const phone = uniquePhone(19);
  await loginCustomer(page, phone);
  await addToCart(page);
  const orderNo = await checkout(page, phone, true);
  let cookies = await page.context().cookies();
  const customerCsrf = cookies.find(
    (cookie) => cookie.name === "afterops_csrf",
  )?.value;
  expect(customerCsrf).toBeTruthy();

  await adminLogin(
    page,
    "owner@afterops.local",
    process.env.DEMO_OWNER_PASSWORD ?? "owner123456",
  );
  cookies = await page.context().cookies();
  const adminCsrf = cookies.find(
    (cookie) => cookie.name === "afterops_admin_csrf",
  )?.value;
  expect(adminCsrf).toBeTruthy();
  const headers = {
    "X-Admin-CSRF-Token": adminCsrf!,
    "X-CSRF-Token": customerCsrf!,
  };
  const trackingNo = `SF-APPEAL-${Date.now()}`;
  expect(
    (
      await page.request.post(
        `http://127.0.0.1:3000/api/admin/orders/${orderNo}/shipment`,
        {
          headers,
          data: { carrier: "顺丰速运", tracking_no: trackingNo },
        },
      )
    ).ok(),
  ).toBeTruthy();
  for (const status of ["in_transit", "out_for_delivery", "delivered"]) {
    expect(
      (
        await page.request.post(
          `http://127.0.0.1:3000/api/admin/orders/${orderNo}/shipment/advance`,
          { headers, data: { status } },
        )
      ).ok(),
    ).toBeTruthy();
  }
  const returnResponse = await page.request.post(
    "http://127.0.0.1:3000/api/store/returns",
    {
      headers,
      data: {
        order_no: orderNo,
        reason: "质检扣减申诉 E2E",
        resolution: "refund",
      },
    },
  );
  expect(returnResponse.ok()).toBeTruthy();
  const returnId = ((await returnResponse.json()) as { id: number }).id;
  expect(
    (
      await page.request.post(
        `http://127.0.0.1:3000/api/admin/returns/${returnId}/approve`,
        { headers },
      )
    ).ok(),
  ).toBeTruthy();
  const received = await page.request.post(
    `http://127.0.0.1:3000/api/admin/returns/${returnId}/receive`,
    {
      headers,
      data: {
        deduction_cents: 1000,
        inspection_note: "缺少原装耳塞套，折价 10 元",
      },
    },
  );
  expect(received.ok()).toBeTruthy();

  await page.goto("/after-sales");
  const returnCard = page.getByTestId(`return-${returnId}`);
  await expect(returnCard).toContainText("质检扣减 10.00 元");
  await expect(returnCard.getByTestId(`appeal-form-${returnId}`)).toBeVisible();
  await returnCard
    .getByTestId(`appeal-reason-${returnId}`)
    .fill("商品实际完好，扣减金额不合理");
  await returnCard.getByTestId(`appeal-files-${returnId}`).setInputFiles({
    name: "appeal.png",
    mimeType: "image/png",
    buffer: Buffer.from(
      "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII=",
      "base64",
    ),
  });
  await returnCard.getByTestId(`submit-appeal-${returnId}`).click();
  await expect(
    page.getByText(`退货 #${returnId} 扣减申诉已提交，等待店主处理`),
  ).toBeVisible();
  const appealCard = page.getByTestId(`return-appeal-${returnId}`);
  await expect(appealCard).toContainText("待处理");
  await expect(appealCard).toContainText("申请 10.00 元");
  await expect(appealCard.locator(".attachment-grid img").first()).toBeVisible();

  await page.goto("/admin");
  await page.getByTestId("tab-returns").click();
  await page.getByTestId("return-filter").selectOption("appeal");
  const adminCard = page.getByTestId(`admin-return-${returnId}`);
  await expect(adminCard).toBeVisible();
  await expect(
    adminCard.getByTestId(`admin-return-appeal-${returnId}`),
  ).toContainText("商品实际完好，扣减金额不合理");
  await adminCard.getByTestId(`appeal-amount-${returnId}`).fill("6");
  await adminCard
    .getByTestId(`appeal-note-${returnId}`)
    .fill("复核后调整扣减");
  await adminCard.getByTestId(`accept-appeal-${returnId}`).click();
  await expect(
    page.getByText(`退货 #${returnId} 扣减申诉已支持，追加退款提案已生成`),
  ).toBeVisible();

  await page.getByTestId("return-filter").selectOption("received");
  await expect(page.getByTestId(`admin-return-${returnId}`)).toContainText(
    "扣减申诉 已支持",
  );
  await expect(page.getByTestId(`admin-return-${returnId}`)).toContainText(
    "核准 6.00 元",
  );

  await page.getByTestId("tab-proposals").click();
  const appealProposal = page
    .locator(".proposal-card")
    .filter({ hasText: orderNo })
    .filter({ hasText: "质检扣减申诉补偿" })
    .first();
  await expect(appealProposal).toBeVisible();
  await expect(appealProposal).toContainText("6.00 元");

  await page.goto("/after-sales");
  const resolvedReturn = page.getByTestId(`return-${returnId}`);
  await expect(resolvedReturn).toContainText("扣减申诉");
  await expect(resolvedReturn).toContainText("已支持");
  await expect(resolvedReturn).toContainText("核准 6.00 元");
  await expect(resolvedReturn).toContainText("处理说明：复核后调整扣减");
});

test("customer schedules a return pickup and owner marks it picked up", async ({
  page,
}) => {
  const phone = uniquePhone(23);
  await loginCustomer(page, phone);
  await addToCart(page);
  const orderNo = await checkout(page, phone, true);
  let cookies = await page.context().cookies();
  const customerCsrf = cookies.find(
    (cookie) => cookie.name === "afterops_csrf",
  )?.value;
  expect(customerCsrf).toBeTruthy();

  await adminLogin(
    page,
    "owner@afterops.local",
    process.env.DEMO_OWNER_PASSWORD ?? "owner123456",
  );
  cookies = await page.context().cookies();
  const adminCsrf = cookies.find(
    (cookie) => cookie.name === "afterops_admin_csrf",
  )?.value;
  expect(adminCsrf).toBeTruthy();
  const headers = {
    "X-Admin-CSRF-Token": adminCsrf!,
    "X-CSRF-Token": customerCsrf!,
  };
  const trackingNo = `SF-PICKUP-OUT-${Date.now()}`;
  expect(
    (
      await page.request.post(
        `http://127.0.0.1:3000/api/admin/orders/${orderNo}/shipment`,
        {
          headers,
          data: { carrier: "顺丰速运", tracking_no: trackingNo },
        },
      )
    ).ok(),
  ).toBeTruthy();
  for (const status of ["in_transit", "out_for_delivery", "delivered"]) {
    expect(
      (
        await page.request.post(
          `http://127.0.0.1:3000/api/admin/orders/${orderNo}/shipment/advance`,
          { headers, data: { status } },
        )
      ).ok(),
    ).toBeTruthy();
  }
  const returnResponse = await page.request.post(
    "http://127.0.0.1:3000/api/store/returns",
    {
      headers,
      data: {
        order_no: orderNo,
        reason: "上门取件 E2E",
        resolution: "refund",
      },
    },
  );
  expect(returnResponse.ok()).toBeTruthy();
  const returnId = ((await returnResponse.json()) as { id: number }).id;
  expect(
    (
      await page.request.post(
        `http://127.0.0.1:3000/api/admin/returns/${returnId}/approve`,
        { headers },
      )
    ).ok(),
  ).toBeTruthy();

  await page.goto("/after-sales");
  const returnCard = page.getByTestId(`return-${returnId}`);
  await expect(returnCard.getByTestId(`pickup-form-${returnId}`)).toBeVisible();
  const pickupDate = new Date();
  pickupDate.setDate(pickupDate.getDate() + 1);
  const month = String(pickupDate.getMonth() + 1).padStart(2, "0");
  const day = String(pickupDate.getDate()).padStart(2, "0");
  const pickupDateValue = `${pickupDate.getFullYear()}-${month}-${day}`;
  await returnCard.getByTestId(`pickup-date-${returnId}`).fill(pickupDateValue);
  await returnCard
    .getByTestId(`pickup-window-${returnId}`)
    .selectOption("13:00-18:00");
  await returnCard.getByTestId(`schedule-pickup-${returnId}`).click();
  await expect(
    page.getByText(`退货 #${returnId} 上门取件已预约`),
  ).toBeVisible();
  const pickupPanel = returnCard.getByTestId(`return-pickup-${returnId}`);
  await expect(pickupPanel).toContainText("待上门");
  await expect(pickupPanel).toContainText(pickupDateValue);
  await expect(pickupPanel).toContainText("13:00-18:00");
  await expect(pickupPanel).toContainText("取件地址");

  const listed = await page.request.get(
    "http://127.0.0.1:3000/api/store/returns",
  );
  const rows = (await listed.json()) as {
    id: number;
    pickup: { pickup_no: string } | null;
  }[];
  const pickupNo = rows.find((row) => row.id === returnId)?.pickup?.pickup_no;
  expect(pickupNo).toBeTruthy();

  await page.goto("/admin");
  await page.getByTestId("tab-returns").click();
  await page.getByTestId("return-filter").selectOption("pickup");
  const adminCard = page.getByTestId(`admin-return-${returnId}`);
  await expect(adminCard).toBeVisible();
  await expect(
    adminCard.getByTestId(`admin-return-pickup-${returnId}`),
  ).toContainText("待上门");
  await expect(
    adminCard.getByTestId(`admin-return-pickup-${returnId}`),
  ).toContainText(pickupNo!);
  await adminCard.getByTestId(`pickup-picked-up-${returnId}`).click();
  await expect(
    page.getByText(`退货 #${returnId} 上门取件已揽收`),
  ).toBeVisible();

  await page.goto("/after-sales");
  const pickedReturn = page.getByTestId(`return-${returnId}`);
  await expect(pickedReturn).toContainText("已揽收");
  await expect(pickedReturn).toContainText(pickupNo!);
  await expect(pickedReturn).toContainText("回寄 顺丰速运");
});

test("customer returns only one item from a multi-item order", async ({
  page,
}) => {
  await adminLogin(
    page,
    "owner@afterops.local",
    process.env.DEMO_OWNER_PASSWORD ?? "owner123456",
  );
  await page.getByTestId("tab-products").click();
  const activeProducts = page
    .locator('article[data-testid^="admin-product-"]')
    .filter({ hasText: "在售" });
  for (const index of [0, 1]) {
    const productCard = activeProducts.nth(index);
    await productCard
      .locator('input[data-testid^="inventory-input-"]')
      .first()
      .fill("10");
    const saved = page.waitForResponse(
      (response) =>
        response.url().includes("/inventory/") &&
        response.request().method() === "PUT" &&
        response.ok(),
    );
    await productCard
      .locator('button[data-testid^="save-inventory-"]')
      .first()
      .click();
    await saved;
  }

  const phone = uniquePhone(22);
  await loginCustomer(page, phone);
  const addButtons = page.locator('[data-testid^="add-"]:not([disabled])');
  expect(await addButtons.count()).toBeGreaterThan(1);
  for (const index of [0, 1]) {
    const added = page.waitForResponse(
      (response) =>
        response.url().includes("/api/store/cart/items") &&
        response.request().method() === "POST" &&
        response.ok(),
    );
    await addButtons.nth(index).click();
    await added;
  }
  const orderNo = await checkout(page, phone, true);

  await adminLogin(
    page,
    "owner@afterops.local",
    process.env.DEMO_OWNER_PASSWORD ?? "owner123456",
  );
  await page.getByTestId("tab-fulfillment").click();
  const fulfillmentCard = page.getByTestId(`fulfillment-order-${orderNo}`);
  await fulfillmentCard
    .getByTestId(`tracking-${orderNo}`)
    .fill(`SF-PARTIAL-${Date.now()}`);
  await fulfillmentCard.getByTestId(`create-shipment-${orderNo}`).click();
  await expect(
    page.getByText(new RegExp(`订单 ${orderNo} 已创建运单`)),
  ).toBeVisible();
  for (const label of ["运输中", "派送中", "已签收"]) {
    await page.getByTestId(`advance-shipment-${orderNo}`).click();
    await expect(
      page.getByText(new RegExp(`订单 ${orderNo} 物流已更新为${label}`)),
    ).toBeVisible();
  }

  await page.goto("/after-sales");
  const itemBoxes = page.locator('[data-testid^="return-item-"]');
  await expect(itemBoxes).toHaveCount(2);
  await itemBoxes.nth(1).uncheck();
  await page.getByTestId("return-reason").fill("只退其中一件");
  await page.getByTestId("create-return").click();
  await expect(page.getByText("退货申请已提交，等待店主审核")).toBeVisible();

  await page.goto("/admin");
  await page.getByTestId("tab-returns").click();
  const adminReturn = page
    .locator(".proposal-card")
    .filter({ hasText: orderNo })
    .first();
  await expect(adminReturn).toContainText(orderNo);
  await expect(
    adminReturn.locator('[data-testid^="admin-return-item-"]'),
  ).toHaveCount(1);

  const returnId = (await adminReturn.getAttribute("data-testid"))?.replace(
    "admin-return-",
    "",
  );
  expect(returnId).toBeTruthy();
  await adminReturn.getByTestId(`approve-return-${returnId}`).click();
  await expect(
    page.getByText(new RegExp(`退货 #${returnId} 已批准`)),
  ).toBeVisible();

  await page.goto("/after-sales");
  const customerReturn = page.getByTestId(`return-${returnId}`);
  await customerReturn
    .getByTestId(`return-carrier-${returnId}`)
    .fill("顺丰速运");
  await customerReturn
    .getByTestId(`return-tracking-${returnId}`)
    .fill(`SF-INSPECT-${Date.now()}`);
  await customerReturn
    .getByTestId(`submit-return-tracking-${returnId}`)
    .click();
  await expect(
    page.getByText(new RegExp(`退货 #${returnId} 已登记回寄单号`)),
  ).toBeVisible();
  await expect(
    page.getByTestId(`customer-return-tracking-${returnId}`),
  ).toContainText("已登记回寄");

  await adminLogin(
    page,
    "owner@afterops.local",
    process.env.DEMO_OWNER_PASSWORD ?? "owner123456",
  );
  await page.getByTestId("tab-returns").click();
  const inspectReturn = page.getByTestId(`admin-return-${returnId}`);
  await inspectReturn
    .getByTestId(`inspection-files-${returnId}`)
    .setInputFiles({
      name: "inspection.png",
      mimeType: "image/png",
      buffer: PNG,
    });
  await expect(
    page.getByText(new RegExp(`退货 #${returnId} 质检照片已上传`)),
  ).toBeVisible();
  await expect(
    inspectReturn
      .getByTestId(`inspection-images-${returnId}`)
      .locator("img"),
  ).toHaveCount(1);
  await inspectReturn
    .getByTestId(`return-exception-${returnId}`)
    .fill("包裹外箱破损，承运商拍照留证");
  await inspectReturn
    .getByTestId(`report-return-exception-${returnId}`)
    .click();
  await expect(
    page.getByText(new RegExp(`退货 #${returnId} 物流异常已登记`)),
  ).toBeVisible();
  await expect(
    page.getByTestId(`return-tracking-events-${returnId}`),
  ).toContainText("物流异常");
  for (const label of ["已揽收", "运输中", "派送中", "已到达退货仓"]) {
    await inspectReturn.getByTestId(`advance-return-tracking-${returnId}`).click();
    await expect(
      page.getByText(new RegExp(`退货 #${returnId} 物流已更新：${label}`)),
    ).toBeVisible();
  }
  await expect(
    page.getByTestId(`return-tracking-events-${returnId}`),
  ).toContainText("已到达退货仓");
  await inspectReturn
    .locator('select[data-testid^="disposition-"]')
    .selectOption("damaged");
  await inspectReturn.getByTestId(`receive-return-${returnId}`).click();
  await expect(
    page.getByText(new RegExp(`退货 #${returnId} 已收货.*报损 1 件`)),
  ).toBeVisible();
  await page.getByTestId("return-filter").selectOption("received");
  await expect(page.getByTestId(`admin-return-${returnId}`)).toContainText(
    "已报损",
  );

  await page.goto("/after-sales");
  const finalReturn = page.getByTestId(`return-${returnId}`);
  await expect(finalReturn).toContainText("（已报损）");
  await expect(
    finalReturn.getByTestId(`customer-return-tracking-${returnId}`),
  ).toContainText("已到达退货仓");
  await expect(
    finalReturn.getByTestId(`customer-return-tracking-${returnId}`),
  ).toContainText("物流异常");
  await expect(
    finalReturn
      .getByTestId(`customer-inspection-images-${returnId}`)
      .locator("img"),
  ).toHaveCount(1);
});

test("partial multi-item exchange creates a replacement order", async ({
  page,
}) => {
  const stamp = Date.now();
  const sourceSku = `SKU-EXA-${stamp}`;
  const targetSku = `SKU-EXB-${stamp}`;
  const adminLoginResponse = await page.request.post(
    "http://127.0.0.1:3000/api/admin/session",
    {
      data: {
        email: "owner@afterops.local",
        password: process.env.DEMO_OWNER_PASSWORD ?? "owner123456",
      },
    },
  );
  expect(adminLoginResponse.ok()).toBeTruthy();
  let cookies = await page.context().cookies();
  const adminCsrf = cookies.find(
    (cookie) => cookie.name === "afterops_admin_csrf",
  )?.value;
  expect(adminCsrf).toBeTruthy();
  const adminHeaders = { "X-Admin-CSRF-Token": adminCsrf! };
  for (const [sku, title, price] of [
    [sourceSku, "E2E 换货原商品", 10000],
    [targetSku, "E2E 换货目标商品", 4000],
  ] as const) {
    const created = await page.request.post(
      "http://127.0.0.1:3000/api/admin/products",
      {
        headers: adminHeaders,
        data: {
          sku,
          title,
          price_cents: price,
          initial_qty: 5,
        },
      },
    );
    expect(created.ok()).toBeTruthy();
  }

  const phone = uniquePhone(35);
  await loginCustomer(page, phone);
  for (const sku of [sourceSku, targetSku]) {
    await page.getByLabel("搜索商品").fill(sku);
    await page.getByRole("button", { name: "搜索" }).click();
    const added = page.waitForResponse(
      (response) =>
        response.url().includes("/api/store/cart/items") &&
        response.request().method() === "POST" &&
        response.ok(),
    );
    await page.getByTestId(`add-${sku}`).click();
    await added;
  }
  const orderNo = await checkout(page, phone, true);

  cookies = await page.context().cookies();
  const customerCsrf = cookies.find(
    (cookie) => cookie.name === "afterops_csrf",
  )?.value;
  expect(customerCsrf).toBeTruthy();
  const requestHeaders = {
    "X-Admin-CSRF-Token": adminCsrf!,
    "X-CSRF-Token": customerCsrf!,
  };
  const trackingNo = `SF-EX-${stamp}`;
  expect(
    (
      await page.request.post(
        `http://127.0.0.1:3000/api/admin/orders/${orderNo}/shipment`,
        {
          headers: requestHeaders,
          data: { carrier: "顺丰速运", tracking_no: trackingNo },
        },
      )
    ).ok(),
  ).toBeTruthy();
  for (const status of ["in_transit", "out_for_delivery", "delivered"]) {
    expect(
      (
        await page.request.post(
          `http://127.0.0.1:3000/api/admin/orders/${orderNo}/shipment/advance`,
          { headers: requestHeaders, data: { status } },
        )
      ).ok(),
    ).toBeTruthy();
  }

  await page.goto("/after-sales");
  await page.getByTestId("return-order").selectOption(orderNo);
  await expect(
    page.locator('[data-testid^="return-item-"]'),
  ).toHaveCount(2);
  await page.getByTestId(`return-item-${sourceSku}`).check();
  await page.getByTestId(`return-item-${targetSku}`).uncheck();
  await page.getByTestId("return-resolution").selectOption("exchange");
  await page
    .getByTestId(`exchange-sku-${sourceSku}`)
    .selectOption(targetSku);
  await page.getByTestId("return-reason").fill("原商品换目标商品");
  await page.getByTestId("create-return").click();
  await expect(page.getByText("退货申请已提交，等待店主审核")).toBeVisible();

  const listed = await page.request.get(
    "http://127.0.0.1:3000/api/admin/returns?status=all",
  );
  expect(listed.ok()).toBeTruthy();
  const returnRow = (
    (await listed.json()) as {
      items: { id: number; order_no: string; exchange_sku: string | null }[];
    }
  ).items.find((row) => row.order_no === orderNo);
  expect(returnRow).toBeTruthy();
  const returnId = returnRow!.id;
  expect(returnRow!.exchange_sku).toBe(targetSku);
  expect(
    (
      await page.request.post(
        `http://127.0.0.1:3000/api/admin/returns/${returnId}/approve`,
        { headers: requestHeaders },
      )
    ).ok(),
  ).toBeTruthy();
  expect(
    (
      await page.request.post(
        `http://127.0.0.1:3000/api/store/returns/${returnId}/tracking`,
        {
          headers: requestHeaders,
          data: { carrier: "顺丰速运", tracking_no: `SF-EX-BACK-${stamp}` },
        },
      )
    ).ok(),
  ).toBeTruthy();
  const received = await page.request.post(
    `http://127.0.0.1:3000/api/admin/returns/${returnId}/receive`,
    { headers: requestHeaders },
  );
  expect(received.ok()).toBeTruthy();
  const receivedBody = (await received.json()) as {
    replacement_order_no: string;
    refund_amount_cents: number;
    items: { exchange_sku: string | null }[];
  };
  expect(receivedBody.refund_amount_cents).toBe(6000);
  expect(receivedBody.items[0].exchange_sku).toBe(targetSku);

  const replacementTracking = `JD-EX-${stamp}`;
  expect(
    (
      await page.request.post(
        `http://127.0.0.1:3000/api/admin/orders/${receivedBody.replacement_order_no}/shipment`,
        {
          headers: requestHeaders,
          data: { carrier: "京东物流", tracking_no: replacementTracking },
        },
      )
    ).ok(),
  ).toBeTruthy();
  expect(
    (
      await page.request.post(
        `http://127.0.0.1:3000/api/admin/orders/${receivedBody.replacement_order_no}/shipment/advance`,
        { headers: requestHeaders, data: { status: "in_transit" } },
      )
    ).ok(),
  ).toBeTruthy();

  await page.goto("/after-sales");
  const returnCard = page.getByTestId(`return-${returnId}`);
  await expect(returnCard).toBeVisible();
  await expect(returnCard.getByText("换货补发")).toBeVisible();
  await expect(
    returnCard.locator(
      `[data-testid^="replacement-shipment-${returnId}-"]`,
    ),
  ).toHaveCount(1);
  await expect(returnCard.getByText(replacementTracking)).toBeVisible();
  await expect(returnCard.getByText("运输中").first()).toBeVisible();

  await page.goto("/admin");
  await page.getByTestId("tab-returns").click();
  await page.getByTestId("return-filter").selectOption("received");
  const adminCard = page.getByTestId(`admin-return-${returnId}`);
  await expect(adminCard).toBeVisible();
  await expect(
    adminCard.locator(
      `[data-testid^="admin-replacement-shipment-${returnId}-"]`,
    ),
  ).toContainText(replacementTracking);

  await page.goto("/orders");
  await expect(
    page.getByText(receivedBody.replacement_order_no).first(),
  ).toBeVisible();
});

test("higher-priced exchange requires supplement payment before shipping", async ({
  page,
}) => {
  const stamp = Date.now();
  const sourceSku = `SKU-HPS-${stamp}`;
  const targetSku = `SKU-HPT-${stamp}`;
  await adminLogin(
    page,
    "owner@afterops.local",
    process.env.DEMO_OWNER_PASSWORD ?? "owner123456",
  );
  let cookies = await page.context().cookies();
  const adminCsrf = cookies.find(
    (cookie) => cookie.name === "afterops_admin_csrf",
  )?.value;
  expect(adminCsrf).toBeTruthy();
  for (const [sku, title, price] of [
    [sourceSku, "E2E 加价换货原商品", 4000],
    [targetSku, "E2E 加价换货目标商品", 10000],
  ] as const) {
    const created = await page.request.post(
      "http://127.0.0.1:3000/api/admin/products",
      {
        headers: { "X-Admin-CSRF-Token": adminCsrf! },
        data: {
          sku,
          title,
          price_cents: price,
          initial_qty: 5,
        },
      },
    );
    expect(created.ok()).toBeTruthy();
  }

  const phone = uniquePhone(25);
  await loginCustomer(page, phone);
  await page.getByLabel("搜索商品").fill(sourceSku);
  await page.getByRole("button", { name: "搜索" }).click();
  const added = page.waitForResponse(
    (response) =>
      response.url().includes("/api/store/cart/items") &&
      response.request().method() === "POST" &&
      response.ok(),
  );
  await page.getByTestId(`add-${sourceSku}`).click();
  await added;
  const orderNo = await checkout(page, phone, true);

  cookies = await page.context().cookies();
  const customerCsrf = cookies.find(
    (cookie) => cookie.name === "afterops_csrf",
  )?.value;
  expect(customerCsrf).toBeTruthy();
  const headers = {
    "X-Admin-CSRF-Token": adminCsrf!,
    "X-CSRF-Token": customerCsrf!,
  };
  const trackingNo = `SF-HP-${stamp}`;
  expect(
    (
      await page.request.post(
        `http://127.0.0.1:3000/api/admin/orders/${orderNo}/shipment`,
        { headers, data: { carrier: "顺丰速运", tracking_no: trackingNo } },
      )
    ).ok(),
  ).toBeTruthy();
  for (const status of ["in_transit", "out_for_delivery", "delivered"]) {
    expect(
      (
        await page.request.post(
          `http://127.0.0.1:3000/api/admin/orders/${orderNo}/shipment/advance`,
          { headers, data: { status } },
        )
      ).ok(),
    ).toBeTruthy();
  }
  const createdReturn = await page.request.post(
    "http://127.0.0.1:3000/api/store/returns",
    {
      headers,
      data: {
        order_no: orderNo,
        reason: "加价换货补差 E2E",
        resolution: "exchange",
        items: [
          { sku: sourceSku, qty: 1, exchange_sku: targetSku },
        ],
      },
    },
  );
  expect(createdReturn.ok()).toBeTruthy();
  const returnId = ((await createdReturn.json()) as { id: number }).id;
  expect(
    (
      await page.request.post(
        `http://127.0.0.1:3000/api/admin/returns/${returnId}/approve`,
        { headers },
      )
    ).ok(),
  ).toBeTruthy();
  const received = await page.request.post(
    `http://127.0.0.1:3000/api/admin/returns/${returnId}/receive`,
    { headers },
  );
  expect(received.ok()).toBeTruthy();
  const receivedBody = (await received.json()) as {
    replacement_order_no: string;
    exchange_supplement_cents: number;
    exchange_payment_status: string;
    refund_proposal_id: number | null;
  };
  expect(receivedBody.exchange_supplement_cents).toBe(6000);
  expect(receivedBody.exchange_payment_status).toBe("pending");
  expect(receivedBody.refund_proposal_id).toBeNull();

  const blocked = await page.request.post(
    `http://127.0.0.1:3000/api/admin/orders/${receivedBody.replacement_order_no}/shipment`,
    {
      headers,
      data: { carrier: "顺丰速运", tracking_no: `SF-HP-SUPP-${stamp}` },
    },
  );
  expect(blocked.status()).toBe(409);
  expect(
    ((await blocked.json()) as { detail: string }).detail,
  ).toContain("补差价");

  await page.goto("/after-sales");
  const returnCard = page.getByTestId(`return-${returnId}`);
  const supplementPanel = returnCard.getByTestId(
    `exchange-supplement-${returnId}`,
  );
  await expect(supplementPanel).toContainText("待支付");
  await expect(supplementPanel).toContainText("60.00 元");
  await returnCard
    .getByTestId(`pay-exchange-supplement-${returnId}`)
    .click();
  await expect(
    page.getByText(new RegExp(`退货 #${returnId} 换货补差价已支付`)),
  ).toBeVisible();
  await expect(supplementPanel).toContainText("已支付");
  await expect(supplementPanel).toContainText("支付单 EXS-");

  const replacementTracking = `SF-HP-OUT-${stamp}`;
  expect(
    (
      await page.request.post(
        `http://127.0.0.1:3000/api/admin/orders/${receivedBody.replacement_order_no}/shipment`,
        {
          headers,
          data: {
            carrier: "顺丰速运",
            tracking_no: replacementTracking,
          },
        },
      )
    ).ok(),
  ).toBeTruthy();
  expect(
    (
      await page.request.post(
        `http://127.0.0.1:3000/api/admin/orders/${receivedBody.replacement_order_no}/shipment/advance`,
        { headers, data: { status: "in_transit" } },
      )
    ).ok(),
  ).toBeTruthy();

  await page.goto("/after-sales");
  await expect(
    page.getByTestId(`return-${returnId}`).getByText(replacementTracking),
  ).toBeVisible();

  await page.goto("/admin");
  await page.getByTestId("tab-finance").click();
  const chargePanel = page.getByTestId("after-sales-charges");
  const chargeCard = chargePanel
    .locator(".proposal-card")
    .filter({ hasText: orderNo })
    .first();
  await expect(chargeCard).toContainText("换货补差");
  await expect(chargeCard).toContainText("60.00");
  await expect(chargeCard).toContainText("EXS-");

  await page.getByTestId("tab-returns").click();
  await page.getByTestId("return-filter").selectOption("received");
  await expect(
    page.getByTestId(`admin-exchange-supplement-${returnId}`),
  ).toContainText("已支付");
});

test("customer abandons exchange supplement and owner refunds", async ({
  page,
}) => {
  const stamp = Date.now();
  const sourceSku = `SKU-ECX-${stamp}`;
  const targetSku = `SKU-ECY-${stamp}`;
  await adminLogin(
    page,
    "owner@afterops.local",
    process.env.DEMO_OWNER_PASSWORD ?? "owner123456",
  );
  let cookies = await page.context().cookies();
  const adminCsrf = cookies.find(
    (cookie) => cookie.name === "afterops_admin_csrf",
  )?.value;
  expect(adminCsrf).toBeTruthy();
  for (const [sku, title, price] of [
    [sourceSku, "E2E 放弃换货原商品", 4000],
    [targetSku, "E2E 放弃换货目标商品", 10000],
  ] as const) {
    const created = await page.request.post(
      "http://127.0.0.1:3000/api/admin/products",
      {
        headers: { "X-Admin-CSRF-Token": adminCsrf! },
        data: {
          sku,
          title,
          price_cents: price,
          initial_qty: 5,
        },
      },
    );
    expect(created.ok()).toBeTruthy();
  }

  const phone = uniquePhone(26);
  await loginCustomer(page, phone);
  await page.getByLabel("搜索商品").fill(sourceSku);
  await page.getByRole("button", { name: "搜索" }).click();
  const added = page.waitForResponse(
    (response) =>
      response.url().includes("/api/store/cart/items") &&
      response.request().method() === "POST" &&
      response.ok(),
  );
  await page.getByTestId(`add-${sourceSku}`).click();
  await added;
  const orderNo = await checkout(page, phone, true);

  cookies = await page.context().cookies();
  const customerCsrf = cookies.find(
    (cookie) => cookie.name === "afterops_csrf",
  )?.value;
  expect(customerCsrf).toBeTruthy();
  const headers = {
    "X-Admin-CSRF-Token": adminCsrf!,
    "X-CSRF-Token": customerCsrf!,
  };
  expect(
    (
      await page.request.post(
        `http://127.0.0.1:3000/api/admin/orders/${orderNo}/shipment`,
        {
          headers,
          data: { carrier: "顺丰速运", tracking_no: `SF-ECX-${stamp}` },
        },
      )
    ).ok(),
  ).toBeTruthy();
  for (const status of ["in_transit", "out_for_delivery", "delivered"]) {
    expect(
      (
        await page.request.post(
          `http://127.0.0.1:3000/api/admin/orders/${orderNo}/shipment/advance`,
          { headers, data: { status } },
        )
      ).ok(),
    ).toBeTruthy();
  }
  const createdReturn = await page.request.post(
    "http://127.0.0.1:3000/api/store/returns",
    {
      headers,
      data: {
        order_no: orderNo,
        reason: "放弃换货补差 E2E",
        resolution: "exchange",
        items: [
          { sku: sourceSku, qty: 1, exchange_sku: targetSku },
        ],
      },
    },
  );
  expect(createdReturn.ok()).toBeTruthy();
  const returnId = ((await createdReturn.json()) as { id: number }).id;
  expect(
    (
      await page.request.post(
        `http://127.0.0.1:3000/api/admin/returns/${returnId}/approve`,
        { headers },
      )
    ).ok(),
  ).toBeTruthy();
  const received = await page.request.post(
    `http://127.0.0.1:3000/api/admin/returns/${returnId}/receive`,
    { headers },
  );
  expect(received.ok()).toBeTruthy();
  const receivedBody = (await received.json()) as {
    replacement_order_no: string;
    exchange_supplement_cents: number;
  };
  expect(receivedBody.exchange_supplement_cents).toBe(6000);

  await page.goto("/admin");
  await page.getByTestId("tab-returns").click();
  await page.getByTestId("return-filter").selectOption("received");
  const adminSupplementCard = page.getByTestId(
    `admin-return-${returnId}`,
  );
  await adminSupplementCard
    .getByTestId(`admin-extend-exchange-${returnId}`)
    .click();
  await expect(
    page.getByText(new RegExp(`退货 #${returnId} 补差支付期限已延长`)),
  ).toBeVisible();
  await expect(
    adminSupplementCard.getByTestId(`admin-exchange-deadline-${returnId}`),
  ).toContainText("超时自动取消");

  await page.goto("/after-sales");
  const returnCard = page.getByTestId(`return-${returnId}`);
  await expect(
    returnCard.getByTestId(`exchange-supplement-${returnId}`),
  ).toContainText("待支付");
  await expect(
    returnCard.getByTestId(`exchange-deadline-${returnId}`),
  ).toContainText("逾期将自动取消换货");
  page.once("dialog", (dialog) => void dialog.accept());
  await returnCard.getByTestId(`cancel-exchange-${returnId}`).click();
  await expect(
    page.getByText(new RegExp(`退货 #${returnId} 换货已取消`)),
  ).toBeVisible();
  const cancelledPanel = returnCard.getByTestId(
    `exchange-cancelled-${returnId}`,
  );
  await expect(cancelledPanel).toContainText("换货已取消");
  await expect(cancelledPanel).toContainText("退款提案 #");
  await expect(cancelledPanel).toContainText("待商家审批");

  const returnsResponse = await page.request.get(
    "http://127.0.0.1:3000/api/store/returns",
  );
  const rows = (await returnsResponse.json()) as {
    id: number;
    status: string;
    refund_proposal_id: number | null;
    refund_amount_cents: number | null;
    replacement_order_no: string | null;
  }[];
  const cancelledRow = rows.find((row) => row.id === returnId);
  expect(cancelledRow?.status).toBe("exchange_cancelled");
  expect(cancelledRow?.refund_amount_cents).toBe(4000);
  expect(cancelledRow?.refund_proposal_id).toBeTruthy();
  expect(cancelledRow?.replacement_order_no).toBe(
    receivedBody.replacement_order_no,
  );

  const blockedAfterCancel = await page.request.post(
    `http://127.0.0.1:3000/api/admin/orders/${receivedBody.replacement_order_no}/shipment`,
    {
      headers,
      data: { carrier: "顺丰速运", tracking_no: `SF-ECX-OUT-${stamp}` },
    },
  );
  expect(blockedAfterCancel.status()).toBe(409);

  await adminLogin(
    page,
    "owner@afterops.local",
    process.env.DEMO_OWNER_PASSWORD ?? "owner123456",
  );
  await page.getByTestId("tab-returns").click();
  await page.getByTestId("return-filter").selectOption("exchange_cancelled");
  const adminCard = page.getByTestId(`admin-return-${returnId}`);
  await expect(adminCard).toBeVisible();
  await expect(
    adminCard.getByTestId(`admin-exchange-supplement-${returnId}`),
  ).toContainText("已取消");
  await expect(
    adminCard.getByTestId(`admin-exchange-cancelled-${returnId}`),
  ).toContainText("退款提案");

  await page.getByTestId("tab-proposals").click();
  const proposalCard = page
    .locator(".proposal-card")
    .filter({ hasText: orderNo })
    .first();
  await expect(proposalCard).toContainText("放弃补差换货退款");
  await proposalCard.getByRole("button", { name: "批准并执行" }).click();
  await expect(page.getByText(/提案 #\d+ 已执行/)).toBeVisible();

  const orderResponse = await page.request.get(
    `http://127.0.0.1:3000/api/admin/orders/${orderNo}`,
  );
  expect(orderResponse.ok()).toBeTruthy();
  const orderBody = (await orderResponse.json()) as {
    status: string;
    payment_status: string;
  };
  expect(orderBody.status).toBe("refunded");
  expect(orderBody.payment_status).toBe("refunded");
});

test("owner sets return policy and customer sees eligibility", async ({
  page,
}) => {
  const stamp = Date.now();
  const sku = `SKU-EAR-${stamp}`;
  await adminLogin(
    page,
    "owner@afterops.local",
    process.env.DEMO_OWNER_PASSWORD ?? "owner123456",
  );
  await page.getByTestId("tab-products").click();
  await page.getByText("新增商品").click();
  await page.locator("#new-sku").fill(sku);
  await page.locator("#new-title").fill(`E2E 降噪耳机 ${stamp}`);
  await page.locator("#new-category").fill("耳机");
  await page.locator("#new-price").fill("199");
  await page.locator("#new-stock").fill("5");
  await page.getByTestId("create-product").click();
  await expect(page.getByText(`商品 ${sku} 已创建`)).toBeVisible();

  const phone = uniquePhone(21);
  await loginCustomer(page, phone);
  await page.getByLabel("搜索商品").fill(sku);
  await page.getByRole("button", { name: "搜索" }).click();
  const added = page.waitForResponse(
    (response) =>
      response.url().includes("/api/store/cart/items") &&
      response.request().method() === "POST" &&
      response.ok(),
  );
  await page.getByTestId(`add-${sku}`).click();
  await added;
  const orderNo = await checkout(page, phone, true);

  await adminLogin(
    page,
    "owner@afterops.local",
    process.env.DEMO_OWNER_PASSWORD ?? "owner123456",
  );
  await page.getByTestId("tab-fulfillment").click();
  const fulfillmentCard = page.getByTestId(`fulfillment-order-${orderNo}`);
  await fulfillmentCard
    .getByTestId(`tracking-${orderNo}`)
    .fill(`SF-POLICY-${Date.now()}`);
  await fulfillmentCard.getByTestId(`create-shipment-${orderNo}`).click();
  await expect(
    page.getByText(new RegExp(`订单 ${orderNo} 已创建运单`)),
  ).toBeVisible();
  for (const label of ["运输中", "派送中", "已签收"]) {
    await page.getByTestId(`advance-shipment-${orderNo}`).click();
    await expect(
      page.getByText(new RegExp(`订单 ${orderNo} 物流已更新为${label}`)),
    ).toBeVisible();
  }

  await page.getByTestId("tab-returns").click();
  await page.getByTestId("return-policy-window").fill("30");
  await page.getByTestId("return-policy-fee").fill("12.00");
  await page.getByTestId("return-policy-categories").fill("耳机");
  await page.getByTestId("return-policy-windows").fill("耳机=30");
  await page.getByTestId("save-return-policy").click();
  await expect(page.getByText("退货政策已保存")).toBeVisible();

  await page.goto("/after-sales");
  await expect(page.getByTestId("return-eligibility")).toContainText(
    "不支持退货类目",
  );
  await expect(page.getByTestId("create-return")).toBeDisabled();

  await page.goto("/admin");
  await page.getByTestId("tab-returns").click();
  await page.getByTestId("return-policy-categories").fill("");
  await page.getByTestId("return-policy-window").fill("7");
  await page.getByTestId("save-return-policy").click();
  await expect(page.getByText("退货政策已保存")).toBeVisible();

  await page.goto("/after-sales");
  await expect(page.getByTestId("return-eligibility")).toContainText(
    "可申请退货",
  );
  await expect(page.getByTestId("return-eligibility")).toContainText(
    "签收后 7 天",
  );
  await expect(page.getByText("类目窗口 30 天")).toBeVisible();
  await expect(page.getByText(/退款将扣除退货运费 12\.00 元/)).toBeVisible();
  await expect(page.getByTestId("create-return")).toBeEnabled();

  await page.goto("/admin");
  await page.getByTestId("tab-returns").click();
  await page.getByTestId("return-policy-fee").fill("0");
  await page.getByTestId("save-return-policy").click();
  await expect(page.getByText("退货政策已保存")).toBeVisible();
});

test("owner publishes a policy version and sees history", async ({ page }) => {
  const stamp = Date.now();
  const entryId = `POLICY-E2E-${stamp}`;
  const topic = `e2e_${stamp}`;
  await adminLogin(
    page,
    "owner@afterops.local",
    process.env.DEMO_OWNER_PASSWORD ?? "owner123456",
  );
  await page.getByTestId("tab-policies").click();
  await page.getByTestId("new-policy").click();
  await page.getByTestId("policy-entry-id").fill(entryId);
  await page.getByTestId("policy-topic").fill(topic);
  await page.getByTestId("policy-title").fill("E2E 保修政策");
  await page.getByTestId("policy-body").fill("非人为损坏保修 12 个月。");
  await page.getByTestId("policy-source").fill("E2E 测试");
  await page.getByTestId("publish-policy").click();
  await expect(page.getByText(`政策 ${entryId}@v1 已发布`)).toBeVisible();

  const card = page.getByTestId(`policy-${entryId}`);
  await expect(card).toContainText("E2E 保修政策");
  await expect(card).toContainText("版本历史（1）");

  await page.getByTestId(`publish-${entryId}`).click();
  await page.getByTestId("policy-title").fill("E2E 保修政策（新版）");
  const tomorrow = new Date(Date.now() + 86_400_000)
    .toISOString()
    .slice(0, 10);
  await page.getByTestId("policy-valid-from").fill(tomorrow);
  await page.getByTestId("publish-policy").click();
  await expect(page.getByText(`政策 ${entryId}@v2 已发布`)).toBeVisible();
  await expect(page.getByTestId(`policy-${entryId}`)).toContainText(
    "版本历史（2）",
  );
});

test("owner creates and disables a staff account", async ({ page }) => {
  const email = `staff-${Date.now()}@afterops.local`;
  await adminLogin(
    page,
    "owner@afterops.local",
    process.env.DEMO_OWNER_PASSWORD ?? "owner123456",
  );
  await page.getByTestId("tab-staff").click();
  const availability = page.getByTestId("handoff-availability");
  const originalAvailability = (await availability.textContent())?.trim();
  await availability.click();
  await expect(availability).not.toHaveText(originalAvailability ?? "");
  await availability.click();
  await expect(availability).toHaveText(originalAvailability ?? "");
  await page.getByTestId("staff-email").fill(email);
  await page.getByTestId("staff-password").fill("staff123456");
  await page.getByTestId("create-staff").click();
  await expect(page.getByText(new RegExp(`员工 ${email} 已创建`))).toBeVisible();
  const card = page.locator(".proposal-card").filter({ hasText: email }).first();
  await expect(card).toContainText("客服");
  await expect(card).toContainText("离线");
  await expect(card).toContainText("暂无心跳");
  await expect(
    page
      .locator('[data-testid^="staff-online-"]')
      .filter({ hasText: "在线" })
      .first(),
  ).toBeVisible();
  const staffTestId = await card.getAttribute("data-testid");
  const staffId = staffTestId?.split("-").pop();
  expect(staffId).toBeTruthy();
  await card.getByTestId(`skill-${staffId}-complaint`).click();
  await expect(
    page.getByText(new RegExp(`员工 ${email} 工单技能已更新`)),
  ).toBeVisible();
  await expect(card).toContainText("投诉");
  await card.getByRole("button", { name: "停用" }).click();
  await expect(page.getByText(new RegExp(`员工 ${email} 已更新`))).toBeVisible();
  await expect(
    page.locator(".proposal-card").filter({ hasText: email }).first(),
  ).toContainText("已停用");

  await expect(page.getByTestId("agent-performance")).toBeVisible();
  await expect(
    page.getByTestId("handoff-assignment-settings"),
  ).toContainText("新工单自动分配");
  await expect(page.getByTestId("agent-performance")).toContainText(
    "未分配待接管",
  );
  await expect(page.getByTestId("agent-performance")).toContainText(
    "满意度",
  );
  await expect(page.getByTestId("agent-performance")).toContainText(
    "平均首响",
  );
  await expect(page.getByTestId("agent-performance")).toContainText(
    "首响 SLA 超时",
  );
  await expect(
    page.locator('[data-testid^="performance-row-"]').first(),
  ).toBeVisible();
  await expect(
    page.locator('[data-testid^="recent-rating-"]').first(),
  ).toBeVisible();
  await page.getByTestId("performance-hours").selectOption("24");
  await expect(page.getByTestId("agent-performance")).toContainText(
    "窗口起点",
  );
});

test("owner creates and receives an in-transit stock transfer", async ({
  page,
}) => {
  const stamp = Date.now();
  const sku = `SKU-TRF-${stamp}`;
  const adminLoginResponse = await page.request.post(
    "http://127.0.0.1:3000/api/admin/session",
    {
      data: {
        email: "owner@afterops.local",
        password: process.env.DEMO_OWNER_PASSWORD ?? "owner123456",
      },
    },
  );
  expect(adminLoginResponse.ok()).toBeTruthy();
  const cookies = await page.context().cookies();
  const adminCsrf = cookies.find(
    (cookie) => cookie.name === "afterops_admin_csrf",
  )?.value;
  expect(adminCsrf).toBeTruthy();
  const created = await page.request.post(
    "http://127.0.0.1:3000/api/admin/products",
    {
      headers: { "X-Admin-CSRF-Token": adminCsrf! },
      data: {
        sku,
        title: "E2E 调拨单商品",
        price_cents: 9900,
        initial_qty: 5,
      },
    },
  );
  expect(created.ok()).toBeTruthy();

  await adminLogin(
    page,
    "owner@afterops.local",
    process.env.DEMO_OWNER_PASSWORD ?? "owner123456",
  );
  await page.getByTestId("tab-products").click();
  await expect(page.getByTestId("stock-transfers")).toBeVisible();
  await page.getByTestId("transfer-order-sku").fill(sku);
  await page.getByTestId("transfer-order-from").fill("default");
  await page.getByTestId("transfer-order-to").fill("east");
  await page.getByTestId("transfer-order-qty").fill("2");
  await page.getByTestId("transfer-order-note").fill("E2E 在途调拨");
  await page.getByTestId("transfer-order-submit").click();
  await expect(
    page.getByText(/调拨单 TRF-\d{8}-[A-Z0-9]+ 已创建，2 件在途/),
  ).toBeVisible();

  const transferRow = page
    .locator('[data-testid^="stock-transfer-"]')
    .filter({ hasText: sku })
    .first();
  await expect(transferRow).toContainText("在途");
  await expect(transferRow).toContainText("default → east");
  const transferTestId = await transferRow.getAttribute("data-testid");
  const transferId = transferTestId?.split("-").pop();
  expect(transferId).toBeTruthy();
  await transferRow.getByTestId(`receive-stock-transfer-${transferId}`).click();
  await expect(
    page.getByText(/调拨单 TRF-\d{8}-[A-Z0-9]+ 已收货，2 件入库/),
  ).toBeVisible();

  await page.reload();
  await page.getByTestId("tab-products").click();
  const productCard = page.getByTestId(`admin-product-${sku}`);
  await expect(
    productCard.getByTestId(`inventory-input-${sku}-default`),
  ).toHaveValue("3");
  await expect(
    productCard.getByTestId(`inventory-input-${sku}-east`),
  ).toHaveValue("2");
});

test("owner creates a product and adjusts inventory for the storefront", async ({
  page,
}) => {
  const sku = `SKU-E2E-${Date.now().toString().slice(-8)}`;
  await adminLogin(
    page,
    "owner@afterops.local",
    process.env.DEMO_OWNER_PASSWORD ?? "owner123456",
  );
  await page.getByTestId("tab-products").click();
  await page.getByText("新增商品", { exact: true }).click();
  await page.getByTestId("new-sku").fill(sku);
  await page.getByTestId("new-title").fill("E2E 氮化镓充电器");
  await page.getByTestId("new-price").fill("59.90");
  await page.getByTestId("new-cost").fill("42.50");
  await page.getByTestId("new-warranty").fill("6");
  await page.getByTestId("new-stock").fill("2");
  await page.getByTestId("create-product").click();
  await expect(page.getByText(new RegExp(`商品 ${sku} 已创建`))).toBeVisible();

  const card = page.getByTestId(`admin-product-${sku}`);
  await expect(card).toContainText("可售 2");
  await expect(card).toContainText("成本 42.50 元");
  await card.getByTestId(`inventory-input-${sku}-default`).fill("7");
  await card.getByTestId(`save-inventory-${sku}-default`).click();
  await expect(
    page.getByText(new RegExp(`${sku} / default 库存已更新为 7`)),
  ).toBeVisible();
  await expect(page.getByTestId(`admin-product-${sku}`)).toContainText(
    "可售 7",
  );

  await card.getByTestId(`inventory-input-${sku}-default`).fill("6");
  await card.getByTestId(`stocktake-inventory-${sku}-default`).click();
  await expect(
    page.getByText(new RegExp(`${sku} / default 盘点完成：7 → 6`)),
  ).toBeVisible();
  await expect(page.getByTestId(`admin-product-${sku}`)).toContainText(
    "可售 6",
  );

  await card.getByTestId(`transfer-inventory-${sku}-default`).click();
  await card.getByTestId(`transfer-target-${sku}-default`).fill("east");
  await card.getByTestId(`transfer-qty-${sku}-default`).fill("2");
  await card.getByTestId(`transfer-note-${sku}-default`).fill("E2E 调拨");
  await card.getByTestId(`transfer-submit-${sku}-default`).click();
  await expect(
    page.getByText(`${sku}：default → east 调拨 2 件完成`),
  ).toBeVisible();
  await expect(card.getByTestId(`inventory-input-${sku}-default`)).toHaveValue(
    "4",
  );
  await expect(card.getByTestId(`inventory-input-${sku}-east`)).toHaveValue(
    "2",
  );

  await page.getByTestId("valuation-sku").fill(sku);
  await page.getByTestId("refresh-valuation").click();
  const defaultValuation = page.getByTestId(`valuation-${sku}-default`);
  await expect(defaultValuation).toContainText("4");
  await expect(defaultValuation).toContainText("170.00 元");
  await expect(page.getByTestId(`valuation-${sku}-east`)).toContainText(
    "85.00 元",
  );

  await card.getByTestId(`edit-product-${sku}`).click();
  await card.getByTestId(`edit-cost-${sku}`).fill("50");
  await card.getByTestId(`save-product-${sku}`).click();
  await expect(page.getByText(`商品 ${sku} 已更新`)).toBeVisible();
  await page.getByTestId("refresh-valuation").click();
  await expect(page.getByTestId(`valuation-${sku}-default`)).toContainText(
    "200.00 元",
  );

  await page.getByTestId("movement-sku").fill(sku);
  await page.getByTestId("load-movements").click();
  const movementTable = page.getByTestId("movement-table");
  await expect(movementTable).toContainText("商品创建");
  await expect(movementTable).toContainText("手工调整");
  await expect(movementTable).toContainText("库存盘点");
  await expect(movementTable).toContainText("调拨调出");
  await expect(movementTable).toContainText("调拨调入");
  await expect(
    movementTable.locator('tbody tr[title="E2E 调拨"]'),
  ).toHaveCount(2);
  await expect(movementTable).toContainText("+2");
  await expect(movementTable).toContainText("+5");
  await expect(movementTable).toContainText("-1");
  await expect(movementTable).toContainText("-2");

  const reconciliation = page.getByTestId("inventory-reconciliation");
  await reconciliation.getByRole("button", { name: "重新对账" }).click();
  await expect(reconciliation).toContainText("账实一致");
  await expect(reconciliation).toContainText("差异 0");

  const [movementDownload] = await Promise.all([
    page.waitForEvent("download"),
    page.getByTestId("movement-export").click(),
  ]);
  expect(movementDownload.suggestedFilename()).toBe(
    "inventory-movements.csv",
  );
  const movementStream = await movementDownload.createReadStream();
  let movementCsv = "";
  for await (const chunk of movementStream) {
    movementCsv += chunk.toString();
  }
  expect(movementCsv).toContain(sku);
  expect(movementCsv).toContain("库存盘点");

  const [inventoryDownload] = await Promise.all([
    page.waitForEvent("download"),
    page.getByTestId("inventory-export").click(),
  ]);
  expect(inventoryDownload.suggestedFilename()).toBe("inventory.csv");
  const inventoryStream = await inventoryDownload.createReadStream();
  let inventoryCsv = "";
  for await (const chunk of inventoryStream) {
    inventoryCsv += chunk.toString();
  }
  expect(inventoryCsv).toContain(sku);

  await page.getByText("批量导入库存 CSV").click();
  await page.getByTestId("inventory-import-file").setInputFiles({
    name: "inventory.csv",
    mimeType: "text/csv",
    buffer: Buffer.from(
      `sku,warehouse_id,warehouse_name,qty_available
${sku},default,,9
`,
    ),
  });
  await expect(
    page.getByText("已处理 1 行库存（变更 1 行）"),
  ).toBeVisible();
  await expect(
    page.getByTestId(`inventory-input-${sku}-default`),
  ).toHaveValue("9");

  await page.getByTestId(`product-image-${sku}`).setInputFiles({
    name: "main.png",
    mimeType: "image/png",
    buffer: PNG,
  });
  await expect(page.getByText(`商品 ${sku} 主图已更新`)).toBeVisible();

  await page.getByTestId(`product-gallery-${sku}`).setInputFiles({
    name: "detail.png",
    mimeType: "image/png",
    buffer: PNG,
  });
  await expect(page.getByText(`商品 ${sku} 详情图已更新`)).toBeVisible();

  await page.goto(`/product/${sku}`);
  await expect(
    page.getByRole("heading", { name: "E2E 氮化镓充电器" }),
  ).toBeVisible();
  await expect(page.getByText(/59\.90/).first()).toBeVisible();
  await expect(page.getByText("保修期")).toBeVisible();
  await expect(page.getByText("6 个月")).toBeVisible();
  await expect(page.locator(".product-image img")).toBeVisible();
  await expect(page.getByTestId("product-gallery").locator("img")).toHaveCount(
    1,
  );

  await page.goto("/admin");
  await page.getByTestId("tab-products").click();
  const createdCard = page.getByTestId(`admin-product-${sku}`);
  await createdCard.getByTestId(`edit-product-${sku}`).click();
  await createdCard.locator("select").last().selectOption("archived");
  await createdCard.getByTestId(`save-product-${sku}`).click();
  await expect(page.getByText(`商品 ${sku} 已更新`)).toBeVisible();
});

test("customer switches between product variants", async ({ page }) => {
  const stamp = Date.now();
  const group = `E2E-VAR-${stamp}`;
  const blackSku = `SKU-VBLK-${stamp}`;
  const whiteSku = `SKU-VWHT-${stamp}`;
  const adminLoginResponse = await page.request.post(
    "http://127.0.0.1:3000/api/admin/session",
    {
      data: {
        email: "owner@afterops.local",
        password: process.env.DEMO_OWNER_PASSWORD ?? "owner123456",
      },
    },
  );
  expect(adminLoginResponse.ok()).toBeTruthy();
  const cookies = await page.context().cookies();
  const adminCsrf = cookies.find(
    (cookie) => cookie.name === "afterops_admin_csrf",
  )?.value;
  expect(adminCsrf).toBeTruthy();
  for (const [sku, label, price] of [
    [blackSku, "曜石黑", 19900],
    [whiteSku, "云雾白", 20900],
  ] as const) {
    const created = await page.request.post(
      "http://127.0.0.1:3000/api/admin/products",
      {
        headers: { "X-Admin-CSRF-Token": adminCsrf! },
        data: {
          sku,
          title: `E2E 变体耳机 ${label}`,
          category: "耳机",
          price_cents: price,
          variant_group: group,
          variant_label: label,
          initial_qty: 5,
        },
      },
    );
    expect(created.ok()).toBeTruthy();
  }

  await loginCustomer(page, uniquePhone(36));
  await page.goto(`/product/${blackSku}`);
  const variants = page.getByTestId("product-variants");
  await expect(variants).toBeVisible();
  await expect(variants.getByTestId(`variant-${whiteSku}`)).toContainText(
    "云雾白",
  );
  await expect(
    variants.getByTestId(`variant-${whiteSku}`),
  ).toContainText("209.00 元");
  await variants.getByTestId(`variant-${whiteSku}`).click();
  await expect(page).toHaveURL(new RegExp(`/product/${whiteSku}$`));
  await expect(
    page.getByRole("heading", { name: "E2E 变体耳机 云雾白" }),
  ).toBeVisible();
  await expect(page.locator(".price").first()).toContainText("209.00");
});

test("owner imports products from CSV", async ({ page }) => {
  const sku = `SKU-IMP-${Date.now().toString().slice(-8)}`;
  await adminLogin(
    page,
    "owner@afterops.local",
    process.env.DEMO_OWNER_PASSWORD ?? "owner123456",
  );
  await page.getByTestId("tab-products").click();
  await page.getByText("批量导入 CSV").click();
  await page.getByTestId("product-import-file").setInputFiles({
    name: "products.csv",
    mimeType: "text/csv",
    buffer: Buffer.from(
      `sku,title,category,price,initial_qty,warranty_months,status\n${sku},批量导入商品,配件,88.50,4,6,active\n`,
    ),
  });
  await expect(page.getByText("已导入 1 个商品")).toBeVisible();
  const card = page.getByTestId(`admin-product-${sku}`);
  await expect(card).toContainText("批量导入商品");
  await expect(card).toContainText("可售 4");
  await expect(card).toContainText("保修 6 个月");
});

test("customer requests a return through chat after delivery", async ({
  page,
}) => {
  const phone = uniquePhone(10);
  await loginCustomer(page, phone);
  await addToCart(page);
  const orderNo = await checkout(page, phone, true);

  await adminLogin(
    page,
    "owner@afterops.local",
    process.env.DEMO_OWNER_PASSWORD ?? "owner123456",
  );
  await page.getByTestId("tab-fulfillment").click();
  const card = page.getByTestId(`fulfillment-order-${orderNo}`);
  await card.getByTestId(`tracking-${orderNo}`).fill(`SFCHAT${Date.now()}`);
  await card.getByTestId(`create-shipment-${orderNo}`).click();
  await expect(
    page.getByText(new RegExp(`订单 ${orderNo} 已创建运单`)),
  ).toBeVisible();
  for (const status of ["运输中", "派送中", "已签收"]) {
    await page.getByTestId(`advance-shipment-${orderNo}`).click();
    await expect(
      page.getByText(new RegExp(`订单 ${orderNo} 物流已更新为${status}`)),
    ).toBeVisible();
  }

  await page.goto("/");
  await page.getByTestId("chat-toggle").click();
  await page.getByTestId("chat-input").fill(`订单 ${orderNo} 耳机有杂音 我要退货`);
  await page.getByTestId("chat-send").click();
  await expect(page.getByText(/已提交退货退款申请/)).toBeVisible();
  await expect(page.getByText(/退货 #\d+ · 退货退款/)).toBeVisible();

  await page.goto("/after-sales");
  const returnCard = page.locator('article[data-testid^="return-"]').first();
  await expect(returnCard).toContainText(orderNo);
  await expect(returnCard).toContainText("退货退款");
});

test("customer withdraws a pending return", async ({ page }) => {
  const phone = uniquePhone(11);
  await loginCustomer(page, phone);
  await addToCart(page);
  const orderNo = await checkout(page, phone, true);

  await adminLogin(
    page,
    "owner@afterops.local",
    process.env.DEMO_OWNER_PASSWORD ?? "owner123456",
  );
  await page.getByTestId("tab-fulfillment").click();
  const fulfillment = page.getByTestId(`fulfillment-order-${orderNo}`);
  await fulfillment
    .getByTestId(`tracking-${orderNo}`)
    .fill(`SFWITHDRAW${Date.now()}`);
  await fulfillment.getByTestId(`create-shipment-${orderNo}`).click();
  await expect(
    page.getByText(new RegExp(`订单 ${orderNo} 已创建运单`)),
  ).toBeVisible();
  for (const status of ["运输中", "派送中", "已签收"]) {
    await page.getByTestId(`advance-shipment-${orderNo}`).click();
    await expect(
      page.getByText(new RegExp(`订单 ${orderNo} 物流已更新为${status}`)),
    ).toBeVisible();
  }

  await page.goto("/after-sales");
  await page.getByTestId("return-order").selectOption(orderNo);
  await page.getByTestId("return-reason").fill("误提交测试");
  await page.getByTestId("create-return").click();
  await expect(page.getByText("退货申请已提交，等待店主审核")).toBeVisible();

  const card = page.locator('article[data-testid^="return-"]').first();
  await expect(card).toContainText("待审核");
  page.once("dialog", (dialog) => void dialog.accept());
  await card.locator('button[data-testid^="withdraw-return-"]').click();
  await expect(page.getByText(/退货 #\d+ 已撤回/)).toBeVisible();
  await expect(card).toContainText("已撤回");
});

test("customer manages address book and checkout reuses a saved address", async ({
  page,
}) => {
  const phone = uniquePhone(9);
  await loginCustomer(page, phone);
  await page.goto("/addresses");
  await expect(page.getByRole("heading", { name: "地址簿" })).toBeVisible();
  await page.getByTestId("address-label").fill("家");
  await page.getByTestId("address-name").fill("地址簿顾客");
  await page.getByTestId("address-phone").fill(phone);
  await page.getByTestId("address-region").fill("华东");
  await page.getByTestId("address-city").fill("上海");
  await page.getByTestId("address-detail").fill("地址簿测试路 1 号");
  await page.getByTestId("address-save").click();
  await expect(page.getByText("地址已保存", { exact: true })).toBeVisible();
  const card = page.locator('article[data-testid^="address-"]').first();
  await expect(card).toContainText("地址簿测试路 1 号");
  await expect(card).toContainText("默认");

  await page.reload();
  await expect(
    page.locator('article[data-testid^="address-"]').first(),
  ).toContainText("地址簿测试路 1 号");

  await page.goto("/");
  await addToCart(page);
  await page.goto("/cart");
  await expect(page.getByTestId("address-select")).toBeVisible();
  await page.getByTestId("address-select").selectOption("");
  await page.locator("#detail").fill("手动临时代码");
  await page.getByTestId("address-select").selectOption({ index: 1 });
  await expect(page.locator("#recipient_name")).toHaveValue("地址簿顾客");
  await expect(page.locator("#detail")).toHaveValue("地址簿测试路 1 号");
});

test("customer edits account name and sees account summary", async ({ page }) => {
  const phone = uniquePhone(16);
  await loginCustomer(page, phone);
  await page.goto("/account");
  await expect(page.getByRole("heading", { name: "我的账户" })).toBeVisible();
  await page.getByTestId("account-name").fill("E2E 账户称呼");
  await page.getByTestId("account-save").click();
  await expect(page.getByText("称呼已保存")).toBeVisible();
  await page.reload();
  await expect(page.getByTestId("account-name")).toHaveValue("E2E 账户称呼");
  await expect(page.getByRole("link", { name: "管理通知邮箱" })).toBeVisible();
});

test("customer requests a repair and owner completes it", async ({ page }) => {
  const phone = uniquePhone(24);
  await loginCustomer(page, phone);
  await addToCart(page);
  const orderNo = await checkout(page, phone, true);

  await adminLogin(
    page,
    "owner@afterops.local",
    process.env.DEMO_OWNER_PASSWORD ?? "owner123456",
  );
  await page.getByTestId("tab-fulfillment").click();
  const fulfillmentCard = page.getByTestId(`fulfillment-order-${orderNo}`);
  await fulfillmentCard
    .getByTestId(`tracking-${orderNo}`)
    .fill(`SF-REPAIR-${Date.now()}`);
  await fulfillmentCard.getByTestId(`create-shipment-${orderNo}`).click();
  await expect(
    page.getByText(new RegExp(`订单 ${orderNo} 已创建运单`)),
  ).toBeVisible();
  for (const label of ["运输中", "派送中", "已签收"]) {
    await page.getByTestId(`advance-shipment-${orderNo}`).click();
    await expect(
      page.getByText(new RegExp(`订单 ${orderNo} 物流已更新为${label}`)),
    ).toBeVisible();
  }

  await page.goto("/after-sales");
  await expect(
    page.getByTestId("repair-sku").locator("option"),
  ).not.toHaveCount(0);
  await expect(page.getByTestId("repair-eligibility")).toContainText(
    "保修至",
  );
  await expect(page.getByTestId("repair-eligibility")).toContainText(
    "签收后",
  );
  await page.getByTestId("repair-issue").fill("左耳无声，已重新配对");
  await page.getByTestId("create-repair").click();
  await expect(
    page.getByText("维修申请已提交，等待店主审核"),
  ).toBeVisible();
  const repairCard = page
    .locator('article[data-testid^="repair-"]')
    .first();
  await expect(repairCard).toContainText("待审核");
  const repairTestId = await repairCard.getAttribute("data-testid");
  const repairId = repairTestId?.split("-")[1];
  expect(repairId).toBeTruthy();

  await page.goto("/admin");
  await page.getByTestId("tab-repairs").click();
  const adminRepair = page.getByTestId(`admin-repair-${repairId}`);
  await expect(adminRepair).toBeVisible();
  await adminRepair.getByTestId(`approve-repair-${repairId}`).click();
  await expect(page.getByText(/已批准，等待顾客回寄/)).toBeVisible();

  await page.goto("/after-sales");
  const approvedRepair = page.getByTestId(`repair-${repairId}`);
  await expect(approvedRepair).toContainText("待回寄");
  await expect(approvedRepair).toContainText("寄回地址");
  await approvedRepair.getByPlaceholder("承运商").fill("顺丰速运");
  await approvedRepair
    .getByPlaceholder("维修回寄单号")
    .fill(`SF-REPAIR-BACK-${Date.now()}`);
  await approvedRepair
    .getByTestId(`repair-tracking-submit-${repairId}`)
    .click();
  await expect(page.getByText(new RegExp(`维修 #${repairId} 已登记回寄`))).toBeVisible();

  await page.goto("/admin");
  await page.getByTestId("tab-repairs").click();
  const receivedRepair = page.getByTestId(`admin-repair-${repairId}`);
  await receivedRepair.getByTestId(`receive-repair-${repairId}`).click();
  await expect(page.getByText(new RegExp(`维修 #${repairId} 已收货`))).toBeVisible();
  await receivedRepair
    .getByTestId(`repair-quote-amount-${repairId}`)
    .fill("58");
  await receivedRepair
    .getByTestId(`repair-quote-note-${repairId}`)
    .fill("更换电池");
  await receivedRepair.getByTestId(`issue-repair-quote-${repairId}`).click();
  await expect(
    page.getByText(new RegExp(`维修 #${repairId} 报价已发送`)),
  ).toBeVisible();

  await page.goto("/after-sales");
  const quotedRepair = page.getByTestId(`repair-${repairId}`);
  const quotePanel = quotedRepair.getByTestId(`repair-quote-${repairId}`);
  await expect(quotePanel).toContainText("待顾客确认");
  await expect(quotePanel).toContainText("58.00 元");
  await expect(quotePanel).toContainText("更换电池");
  await quotedRepair
    .getByTestId(`repair-quote-decline-${repairId}`)
    .click();
  await expect(
    page.getByText(new RegExp(`维修 #${repairId} 报价已拒绝`)),
  ).toBeVisible();

  await page.goto("/admin");
  await page.getByTestId("tab-repairs").click();
  const declinedRepair = page.getByTestId(`admin-repair-${repairId}`);
  await expect(declinedRepair).toContainText("顾客已拒绝报价");
  await declinedRepair
    .getByTestId(`repair-quote-amount-${repairId}`)
    .fill("48");
  await declinedRepair
    .getByTestId(`repair-quote-note-${repairId}`)
    .fill("更换电池并清理触点");
  await declinedRepair
    .getByTestId(`issue-repair-quote-${repairId}`)
    .click();
  await expect(
    page.getByText(new RegExp(`维修 #${repairId} 报价已更新`)),
  ).toBeVisible();

  await page.goto("/after-sales");
  const revisedRepair = page.getByTestId(`repair-${repairId}`);
  const revisedQuotePanel = revisedRepair.getByTestId(
    `repair-quote-${repairId}`,
  );
  await expect(revisedQuotePanel).toContainText("待顾客确认");
  await expect(revisedQuotePanel).toContainText("已更新");
  await expect(revisedQuotePanel).toContainText("48.00 元");
  await expect(revisedQuotePanel).toContainText("更换电池并清理触点");
  await revisedRepair
    .getByTestId(`repair-quote-accept-${repairId}`)
    .click();
  await expect(
    page.getByText(new RegExp(`维修 #${repairId} 报价已接受`)),
  ).toBeVisible();
  await revisedRepair.getByTestId(`repair-quote-pay-${repairId}`).click();
  await expect(
    page.getByText(new RegExp(`维修 #${repairId} 维修费已支付`)),
  ).toBeVisible();
  await expect(revisedQuotePanel).toContainText("维修费已支付");
  await expect(revisedQuotePanel).toContainText("支付单 RPQ-");

  await page.goto("/admin");
  await page.getByTestId("tab-repairs").click();
  const paidRepair = page.getByTestId(`admin-repair-${repairId}`);
  await expect(
    paidRepair.getByTestId(`admin-repair-quote-${repairId}`),
  ).toContainText("维修费已支付");
  await paidRepair
    .getByTestId(`repair-completion-${repairId}`)
    .fill("已更换电池并回寄");
  await paidRepair
    .getByTestId(`repair-return-carrier-${repairId}`)
    .fill("顺丰速运");
  await paidRepair
    .getByTestId(`repair-return-tracking-${repairId}`)
    .fill(`SF-REPAIR-OUT-${Date.now()}`);
  await paidRepair
    .getByTestId(`repair-parts-cost-${repairId}`)
    .fill("20");
  await paidRepair
    .getByTestId(`repair-parts-note-${repairId}`)
    .fill("电池配件");
  await paidRepair.getByTestId(`complete-repair-${repairId}`).click();
  await expect(
    page.getByText(new RegExp(`维修 #${repairId} 已完成并寄回`)),
  ).toBeVisible();
  await expect(
    paidRepair.getByTestId(`repair-parts-cost-view-${repairId}`),
  ).toContainText("配件成本 20.00 元");
  await expect(
    paidRepair.getByTestId(`repair-parts-cost-view-${repairId}`),
  ).toContainText("维修毛利 28.00 元");

  await page.goto("/after-sales");
  const shippedRepair = page.getByTestId(`repair-${repairId}`);
  await expect(shippedRepair).toContainText("维修寄回中");
  await expect(shippedRepair).toContainText("维修寄回 顺丰速运");
  await expect(shippedRepair).toContainText("已更换电池并回寄");
  await shippedRepair.getByTestId(`repair-confirm-${repairId}`).click();
  await expect(
    page.getByText(new RegExp(`维修 #${repairId} 已确认收到`)),
  ).toBeVisible();
  await expect(page.getByTestId(`repair-${repairId}`)).toContainText(
    "已完成",
  );
  await expect(page.getByTestId(`repair-${repairId}`)).toContainText(
    "已更换电池并回寄",
  );
  const repairTimeline = page.getByTestId(
    "customer-after-sales-timeline",
  );
  await expect(repairTimeline).toContainText("维修报价已更新");
  await expect(repairTimeline).toContainText("维修费已支付");
});

test("owner creates a coupon and customer checks out with it", async ({
  page,
}) => {
  const code = `E2E50-${Date.now().toString().slice(-8)}`;
  await adminLogin(
    page,
    "owner@afterops.local",
    process.env.DEMO_OWNER_PASSWORD ?? "owner123456",
  );
  await page.getByTestId("tab-coupons").click();
  await page.getByTestId("coupon-code-input").fill(code);
  await page.getByTestId("coupon-value").fill("50");
  await page.getByTestId("coupon-claimable").check();
  await page.getByTestId("create-coupon").click();
  await expect(page.getByText(`优惠券 ${code} 已创建`)).toBeVisible();
  const claimableCard = page
    .locator(".proposal-card")
    .filter({ hasText: code })
    .first();
  const claimableId = (
    await claimableCard.getAttribute("data-testid")
  )?.replace("coupon-", "");
  expect(claimableId).toBeTruthy();

  const scopedCode = `EARCAT-${Date.now().toString().slice(-8)}`;
  await page.getByTestId("coupon-code-input").fill(scopedCode);
  await page.getByTestId("coupon-scope-type").selectOption("category");
  await page.getByTestId("coupon-scope-values").fill("耳机");
  await page.getByTestId("create-coupon").click();
  await expect(
    page.getByText(`优惠券 ${scopedCode} 已创建`),
  ).toBeVisible();
  const scopedCard = page
    .locator(".proposal-card")
    .filter({ hasText: scopedCode })
    .first();
  await expect(scopedCard).toContainText("类目：耳机");

  const phone = uniquePhone(25);
  await loginCustomer(page, phone);
  await page.goto("/coupons");
  await page.getByTestId(`claim-coupon-${claimableId}`).click();
  await expect(page.getByTestId("coupon-notice")).toContainText(
    `已领取 ${code}`,
  );
  await expect(page.getByTestId(`wallet-coupon-${claimableId}`)).toContainText(
    code,
  );
  await page.goto("/");
  await addToCart(page);
  const orderNo = await checkout(page, phone, true, code);
  const orderCard = page.getByTestId(`order-${orderNo}`);
  await expect(orderCard).toBeVisible();
  await orderCard.getByRole("button", { name: "查看详情" }).click();
  await expect(orderCard).toContainText("优惠 -50.00 元");

  await page.goto("/admin");
  await page.getByTestId("tab-coupons").click();
  const couponCard = page
    .locator(".proposal-card")
    .filter({ hasText: code })
    .first();
  await expect(couponCard).toContainText("已用 1 / 100");
  await expect(couponCard).toContainText("全场商品");
  await expect(couponCard).toContainText("领券中心 · 已领 1");
});

test("owner creates a shipping method and customer pays freight", async ({
  page,
}) => {
  const code = `E2E-EXPRESS-${Date.now().toString().slice(-6)}`;
  await adminLogin(
    page,
    "owner@afterops.local",
    process.env.DEMO_OWNER_PASSWORD ?? "owner123456",
  );
  await page.getByTestId("tab-shipping").click();
  await page.getByTestId("shipping-code").fill(code);
  await page.getByTestId("shipping-name").fill("E2E 顺丰加急");
  await page.getByTestId("shipping-fee").fill("12");
  await page.getByTestId("create-shipping-method").click();
  await expect(page.getByText("E2E 顺丰加急 已创建")).toBeVisible();

  const phone = uniquePhone(26);
  await loginCustomer(page, phone);
  await addToCart(page);
  const orderNo = await checkout(page, phone, true, undefined, code);
  const orderCard = page.getByTestId(`order-${orderNo}`);
  await orderCard.getByRole("button", { name: "查看详情" }).click();
  await expect(orderCard).toContainText("运费 12.00 元");
  await expect(orderCard).toContainText("应付");
});

test("customer requests an invoice and owner issues it", async ({ page }) => {
  const phone = uniquePhone(27);
  await loginCustomer(page, phone);
  await addToCart(page);
  const orderNo = await checkout(page, phone, true);

  const orderCard = page.getByTestId(`order-${orderNo}`);
  await orderCard.getByRole("button", { name: "查看详情" }).click();
  await page.getByTestId(`request-invoice-${orderNo}`).click();
  await page.getByTestId(`invoice-title-${orderNo}`).fill("E2E 发票抬头");
  await page.getByTestId(`submit-invoice-${orderNo}`).click();
  await expect(page.getByText("发票申请已提交，等待店主开票")).toBeVisible();
  await expect(page.getByTestId(`invoice-status-${orderNo}`)).toContainText(
    "待开票",
  );

  await adminLogin(
    page,
    "owner@afterops.local",
    process.env.DEMO_OWNER_PASSWORD ?? "owner123456",
  );
  await page.getByTestId("tab-finance").click();
  const invoiceCard = page
    .locator('article[data-testid^="invoice-"]')
    .filter({ hasText: orderNo })
    .first();
  await expect(invoiceCard).toBeVisible();
  await invoiceCard
    .locator('[data-testid^="invoice-no-"]')
    .fill(`INV-E2E-${Date.now()}`);
  await invoiceCard.locator('[data-testid^="issue-invoice-"]').click();
  await expect(page.getByText(/发票 #\d+ 已开具/)).toBeVisible();

  await page.goto("/orders");
  const customerCard = page.getByTestId(`order-${orderNo}`);
  await customerCard.getByRole("button", { name: "查看详情" }).click();
  await expect(page.getByTestId(`invoice-status-${orderNo}`)).toContainText(
    "已开票",
  );
  await expect(page.getByTestId(`invoice-status-${orderNo}`)).toContainText(
    "INV-E2E-",
  );

  await adminLogin(
    page,
    "owner@afterops.local",
    process.env.DEMO_OWNER_PASSWORD ?? "owner123456",
  );
  await page.getByTestId("tab-finance").click();
  await page.getByTestId("invoice-filter").selectOption("issued");
  const issuedCard = page
    .locator('article[data-testid^="invoice-"]')
    .filter({ hasText: orderNo })
    .first();
  await issuedCard
    .locator('[data-testid^="void-reason-"]')
    .fill("税号抬头录入错误");
  await issuedCard.locator('[data-testid^="void-invoice-"]').click();
  await expect(page.getByText(/发票 #\d+ 已作废/)).toBeVisible();

  await page.goto("/orders");
  const voidedCard = page.getByTestId(`order-${orderNo}`);
  await voidedCard.getByRole("button", { name: "查看详情" }).click();
  await expect(page.getByTestId(`invoice-status-${orderNo}`)).toContainText(
    "已作废",
  );
  await expect(page.getByTestId(`invoice-status-${orderNo}`)).toContainText(
    "税号抬头录入错误",
  );
  await page.getByTestId(`request-invoice-${orderNo}`).click();
  await page.getByTestId(`invoice-title-${orderNo}`).fill("E2E 重开抬头");
  await page.getByTestId(`submit-invoice-${orderNo}`).click();
  await expect(page.getByText("发票申请已提交，等待店主开票")).toBeVisible();
  await expect(page.getByTestId(`invoice-status-${orderNo}`)).toContainText(
    "待开票",
  );

  await adminLogin(
    page,
    "owner@afterops.local",
    process.env.DEMO_OWNER_PASSWORD ?? "owner123456",
  );
  await page.getByTestId("tab-finance").click();
  const reissueCard = page
    .locator('article[data-testid^="invoice-"]')
    .filter({ hasText: orderNo })
    .first();
  await reissueCard
    .locator('[data-testid^="invoice-no-"]')
    .fill(`INV-E2E-NEW-${Date.now()}`);
  await reissueCard.locator('[data-testid^="issue-invoice-"]').click();
  await expect(page.getByText(/发票 #\d+ 已开具/)).toBeVisible();

  await page.goto("/orders");
  const reissuedCard = page.getByTestId(`order-${orderNo}`);
  await reissuedCard.getByRole("button", { name: "查看详情" }).click();
  await expect(page.getByTestId(`invoice-status-${orderNo}`)).toContainText(
    "已开票",
  );
  await expect(page.getByTestId(`invoice-status-${orderNo}`)).toContainText(
    "INV-E2E-NEW-",
  );

  await adminLogin(
    page,
    "owner@afterops.local",
    process.env.DEMO_OWNER_PASSWORD ?? "owner123456",
  );
  await page.getByText("代顾客发起售后动作").click();
  await page.getByTestId("manual-action-order").fill(orderNo);
  await page.getByTestId("manual-action-amount").fill("10");
  await page.getByTestId("manual-action-reason").fill("开票后部分退款");
  await page.getByTestId("manual-action-submit").click();
  await expect(page.getByText(/退款提案 #\d+ 已创建/)).toBeVisible();
  const refundProposal = page
    .locator(".proposal-card")
    .filter({ hasText: orderNo })
    .first();
  await refundProposal.getByRole("button", { name: "批准并执行" }).click();
  await expect(page.getByText(/提案 #\d+ 已执行/)).toBeVisible();

  await page.getByTestId("tab-finance").click();
  await page.getByTestId("invoice-filter").selectOption("issued");
  const adjustedCard = page
    .locator('article[data-testid^="invoice-"]')
    .filter({ hasText: orderNo })
    .first();
  await expect(adjustedCard).toContainText("需重开");
  await expect(adjustedCard).toContainText("净支付");
  await page.getByTestId("invoice-filter").selectOption("needs_adjustment");
  await expect(
    page
      .locator('article[data-testid^="invoice-"]')
      .filter({ hasText: orderNo })
      .first(),
  ).toContainText("需重开");

  await page.goto("/orders");
  const adjustedOrder = page.getByTestId(`order-${orderNo}`);
  await adjustedOrder.getByRole("button", { name: "查看详情" }).click();
  await expect(page.getByTestId(`invoice-status-${orderNo}`)).toContainText(
    "退款后需重开",
  );
  await expect(page.getByTestId(`invoice-status-${orderNo}`)).toContainText(
    "当前净支付",
  );
  await page.getByTestId("notification-bell").click();
  await expect(page.getByText("发票金额需调整").first()).toBeVisible();
});

test("customer reads help center and opens a policy citation", async ({
  page,
}) => {
  await page.goto("/help");
  await expect(
    page.getByRole("heading", { name: "售后政策与帮助" }),
  ).toBeVisible();
  const returnPolicy = page.getByTestId("help-policy-POLICY-RETURN");
  await expect(returnPolicy).toContainText("7 天无理由退货（现行）");
  await expect(returnPolicy).toContainText("v2");
  await page.getByTestId("help-search").fill("退款到账");
  await expect(
    page.getByTestId("help-policy-POLICY-REFUND-TIME"),
  ).toContainText("退款到账时效");
  await expect(returnPolicy).toHaveCount(0);
  await page.getByTestId("help-search").fill("");

  const phone = uniquePhone(23);
  await loginCustomer(page, phone);
  await page.getByTestId("chat-toggle").click();
  await page.getByTestId("chat-input").fill("耳机能退吗");
  await page.getByTestId("chat-send").click();
  const citation = page
    .getByTestId("policy-citation-link")
    .filter({ hasText: "POLICY-RETURN" })
    .first();
  await expect(citation).toBeVisible();
  await citation.click();
  await expect(page).toHaveURL(/\/help#policy-POLICY-RETURN/);
  await expect(returnPolicy).toBeVisible();
});

test("customer saves and clears a notification email", async ({ page }) => {
  const phone = uniquePhone(8);
  const email = `customer-${Date.now()}@example.com`;
  await loginCustomer(page, phone);
  await page.goto("/after-sales");
  await expect(page.getByTestId("notify-email")).toBeVisible();
  await page.getByTestId("notify-email").fill(email);
  await page.getByTestId("notify-email-return").uncheck();
  await page.getByTestId("notify-email-save").click();
  await expect(page.getByText("通知邮箱已保存")).toBeVisible();
  await page.reload();
  await expect(page.getByTestId("notify-email")).toHaveValue(email);
  await expect(page.getByTestId("notify-email-return")).not.toBeChecked();
  await page.getByRole("button", { name: "停用邮件" }).click();
  await expect(page.getByText("通知邮箱已停用")).toBeVisible();
  await page.reload();
  await expect(page.getByTestId("notify-email")).toHaveValue("");
});

test("multi-package shipping splits items and completes after all packages", async ({
  page,
}) => {
  const stamp = Date.now();
  const skuA = `SKU-MPA-${stamp}`;
  const skuB = `SKU-MPB-${stamp}`;
  const adminLoginResponse = await page.request.post(
    "http://127.0.0.1:3000/api/admin/session",
    {
      data: {
        email: "owner@afterops.local",
        password: process.env.DEMO_OWNER_PASSWORD ?? "owner123456",
      },
    },
  );
  expect(adminLoginResponse.ok()).toBeTruthy();
  const cookies = await page.context().cookies();
  const adminCsrf = cookies.find(
    (cookie) => cookie.name === "afterops_admin_csrf",
  )?.value;
  expect(adminCsrf).toBeTruthy();
  for (const [sku, title] of [
    [skuA, "E2E 拆包商品 A"],
    [skuB, "E2E 拆包商品 B"],
  ] as const) {
    const created = await page.request.post(
      "http://127.0.0.1:3000/api/admin/products",
      {
        headers: { "X-Admin-CSRF-Token": adminCsrf! },
        data: {
          sku,
          title,
          price_cents: 5000,
          initial_qty: 3,
        },
      },
    );
    expect(created.ok()).toBeTruthy();
  }

  const phone = uniquePhone(9);
  await loginCustomer(page, phone);
  for (const sku of [skuA, skuB]) {
    await page.getByLabel("搜索商品").fill(sku);
    await page.getByRole("button", { name: "搜索" }).click();
    const added = page.waitForResponse(
      (response) =>
        response.url().includes("/api/store/cart/items") &&
        response.request().method() === "POST" &&
        response.ok(),
    );
    await page.getByTestId(`add-${sku}`).click();
    await added;
  }
  const orderNo = await checkout(page, phone, true);

  await adminLogin(
    page,
    "owner@afterops.local",
    process.env.DEMO_OWNER_PASSWORD ?? "owner123456",
  );
  await page.getByTestId("tab-fulfillment").click();
  const card = page.getByTestId(`fulfillment-order-${orderNo}`);
  await expect(card).toBeVisible();
  await expect(card).toContainText(`${skuA}：已发 0/1，剩余 1`);
  await expect(card).toContainText(`${skuB}：已发 0/1，剩余 1`);

  const listedResponse = await page.request.get(
    "http://127.0.0.1:3000/api/admin/orders?open_only=true",
  );
  expect(listedResponse.ok()).toBeTruthy();
  const listed = (await listedResponse.json()) as {
    order_no: string;
    items: { order_item_id: number; sku: string }[];
  }[];
  const listedOrder = listed.find((row) => row.order_no === orderNo);
  expect(listedOrder).toBeTruthy();
  const itemId = (sku: string) =>
    listedOrder!.items.find((item) => item.sku === sku)!.order_item_id;

  await card.getByTestId(`tracking-${orderNo}`).fill(`SF-MPA-${stamp}`);
  await card
    .getByTestId(`ship-qty-${orderNo}-${itemId(skuB)}`)
    .fill("0");
  await card.getByTestId(`create-shipment-${orderNo}`).click();
  await expect(
    page.getByText(new RegExp(`订单 ${orderNo} 已创建运单（包裹 #1）`)),
  ).toBeVisible();
  const firstPackage = page.getByTestId(
    `fulfillment-package-${orderNo}-1`,
  );
  await expect(firstPackage).toContainText(`SF-MPA-${stamp}`);
  await expect(firstPackage).toContainText(`${skuA} × 1`);

  for (const status of ["运输中", "派送中", "已签收"]) {
    await page.getByTestId(`advance-shipment-${orderNo}`).click();
    await expect(
      page.getByText(
        new RegExp(`订单 ${orderNo} 物流已更新为${status}`),
      ),
    ).toBeVisible();
  }
  await expect(card).toContainText("仍有商品未发货");
  await expect(card).toContainText(`1 个包裹`);

  await card.getByTestId(`tracking-${orderNo}`).fill(`SF-MPB-${stamp}`);
  await card.getByTestId(`create-shipment-${orderNo}`).click();
  await expect(
    page.getByText(new RegExp(`订单 ${orderNo} 已创建运单（包裹 #2）`)),
  ).toBeVisible();
  const secondPackage = page.getByTestId(
    `fulfillment-package-${orderNo}-2`,
  );
  await expect(secondPackage).toContainText(`SF-MPB-${stamp}`);
  await expect(secondPackage).toContainText(`${skuB} × 1`);

  for (const status of ["运输中", "派送中", "已签收"]) {
    await page.getByTestId(`advance-shipment-${orderNo}-2`).click();
    await expect(
      page.getByText(
        new RegExp(`订单 ${orderNo} 物流已更新为${status}`),
      ),
    ).toBeVisible();
  }

  await page.getByRole("button", { name: "全部订单" }).click();
  const completedCard = page.getByTestId(`fulfillment-order-${orderNo}`);
  await expect(completedCard).toContainText("2 个包裹");
  await expect(completedCard).toContainText("已签收");

  await page.goto("/orders");
  const orderCard = page.getByTestId(`order-${orderNo}`);
  await expect(orderCard.getByText("已签收").first()).toBeVisible();
  await orderCard.getByRole("button", { name: "查看详情" }).click();
  const customerFirst = page.getByTestId(
    `customer-package-${orderNo}-1`,
  );
  const customerSecond = page.getByTestId(
    `customer-package-${orderNo}-2`,
  );
  await expect(customerFirst).toContainText(`SF-MPA-${stamp}`);
  await expect(customerFirst).toContainText(`${skuA} × 1`);
  await expect(customerSecond).toContainText(`SF-MPB-${stamp}`);
  await expect(customerSecond).toContainText(`${skuB} × 1`);
  await expect(page.getByTestId(`shipment-timeline-${orderNo}`)).toContainText(
    "2 个包裹",
  );

  await orderCard.getByRole("button", { name: "确认收货" }).click();
  await expect(
    page.getByText(new RegExp(`订单 ${orderNo} 已确认收货`)),
  ).toBeVisible();
  await expect(orderCard.getByText("已完成").first()).toBeVisible();
});

test("customer partially cancels an unshipped item and owner ships the rest", async ({
  page,
}) => {
  const stamp = Date.now();
  const skuA = `SKU-PCA-${stamp}`;
  const skuB = `SKU-PCB-${stamp}`;
  const adminLoginResponse = await page.request.post(
    "http://127.0.0.1:3000/api/admin/session",
    {
      data: {
        email: "owner@afterops.local",
        password: process.env.DEMO_OWNER_PASSWORD ?? "owner123456",
      },
    },
  );
  expect(adminLoginResponse.ok()).toBeTruthy();
  const cookies = await page.context().cookies();
  const adminCsrf = cookies.find(
    (cookie) => cookie.name === "afterops_admin_csrf",
  )?.value;
  expect(adminCsrf).toBeTruthy();
  for (const [sku, title, price] of [
    [skuA, "E2E 部分取消保留商品", 5000],
    [skuB, "E2E 部分取消取消商品", 3000],
  ] as const) {
    const created = await page.request.post(
      "http://127.0.0.1:3000/api/admin/products",
      {
        headers: { "X-Admin-CSRF-Token": adminCsrf! },
        data: {
          sku,
          title,
          price_cents: price,
          initial_qty: 3,
        },
      },
    );
    expect(created.ok()).toBeTruthy();
  }

  const phone = uniquePhone(10);
  await loginCustomer(page, phone);
  for (const sku of [skuA, skuB]) {
    await page.getByLabel("搜索商品").fill(sku);
    await page.getByRole("button", { name: "搜索" }).click();
    const added = page.waitForResponse(
      (response) =>
        response.url().includes("/api/store/cart/items") &&
        response.request().method() === "POST" &&
        response.ok(),
    );
    await page.getByTestId(`add-${sku}`).click();
    await added;
  }
  const orderNo = await checkout(page, phone, true);

  const orderCard = page.getByTestId(`order-${orderNo}`);
  await orderCard.getByRole("button", { name: "查看详情" }).click();
  await expect(
    page.getByTestId(`partial-cancel-${orderNo}`),
  ).toBeVisible();
  await page.getByTestId(`partial-cancel-open-${orderNo}`).click();
  await page
    .getByTestId(`partial-cancel-qty-${orderNo}-${skuB}`)
    .fill("1");
  await page.getByTestId(`partial-cancel-submit-${orderNo}`).click();
  await expect(
    page.getByText(
      new RegExp(
        `订单 ${orderNo} 已提交取消申请，预计退款 30.00 元，等待店主审批`,
      ),
    ),
  ).toBeVisible();
  await expect(
    page.getByText("取消申请待审核").first(),
  ).toBeVisible();

  await adminLogin(
    page,
    "owner@afterops.local",
    process.env.DEMO_OWNER_PASSWORD ?? "owner123456",
  );
  const proposalCard = page
    .locator(".proposal-card")
    .filter({ hasText: orderNo })
    .first();
  await expect(proposalCard).toContainText("取消未发货商品");
  await expect(proposalCard).toContainText("30.00");
  await proposalCard.getByRole("button", { name: "批准并执行" }).click();
  await expect(page.getByText(/提案 #\d+ 已执行/)).toBeVisible();

  await page.goto("/orders");
  const refreshedCard = page.getByTestId(`order-${orderNo}`);
  await refreshedCard.getByRole("button", { name: "查看详情" }).click();
  const canceledRow = page
    .locator("table.table tbody tr")
    .filter({ hasText: skuB })
    .first();
  await expect(canceledRow.locator("td").nth(3)).toHaveText("1");
  const refundLedger = page.getByTestId(`refund-ledger-${orderNo}`);
  await expect(refundLedger).toContainText("累计退款 30.00 元");
  await expect(refundLedger).toContainText("净支付 50.00 元");
  await expect(refundLedger).toContainText("取消未发货商品");
  await expect(refundLedger).toContainText("已执行");
  await page.getByTestId("chat-toggle").click();
  await page.getByTestId("chat-input").fill(`订单 ${orderNo} 退款进度`);
  await page.getByTestId("chat-send").click();
  await expect(page.getByText(/退款申请已执行/).first()).toBeVisible();
  await expect(page.getByText(/取消未发货商品/).last()).toBeVisible();
  await page.getByTestId("chat-toggle").click();
  await page.getByTestId(`partial-cancel-open-${orderNo}`).click();
  await expect(
    page.getByTestId(`partial-cancel-qty-${orderNo}-${skuB}`),
  ).toHaveCount(0);
  await expect(
    page.getByTestId(`partial-cancel-qty-${orderNo}-${skuA}`),
  ).toBeVisible();

  await adminLogin(
    page,
    "owner@afterops.local",
    process.env.DEMO_OWNER_PASSWORD ?? "owner123456",
  );
  await page.getByTestId("tab-fulfillment").click();
  const fulfillmentCard = page.getByTestId(`fulfillment-order-${orderNo}`);
  await expect(fulfillmentCard).toContainText(`${skuB}：已发 0/1，已取消 1`);
  await fulfillmentCard
    .getByTestId(`tracking-${orderNo}`)
    .fill(`SF-PC-${stamp}`);
  await fulfillmentCard.getByTestId(`create-shipment-${orderNo}`).click();
  await expect(
    page.getByText(new RegExp(`订单 ${orderNo} 已创建运单（包裹 #1）`)),
  ).toBeVisible();
  const packageCard = page.getByTestId(
    `fulfillment-package-${orderNo}-1`,
  );
  await expect(packageCard).toContainText(`${skuA} × 1`);
  await expect(packageCard).not.toContainText(`${skuB} × 1`);

  for (const status of ["运输中", "派送中", "已签收"]) {
    await page.getByTestId(`advance-shipment-${orderNo}`).click();
    await expect(
      page.getByText(
        new RegExp(`订单 ${orderNo} 物流已更新为${status}`),
      ),
    ).toBeVisible();
  }

  await page.goto("/orders");
  const deliveredCard = page.getByTestId(`order-${orderNo}`);
  await expect(deliveredCard.getByText("已签收").first()).toBeVisible();
  await deliveredCard.getByRole("button", { name: "确认收货" }).click();
  await expect(
    page.getByText(new RegExp(`订单 ${orderNo} 已确认收货`)),
  ).toBeVisible();
  await expect(deliveredCard.getByText("已完成").first()).toBeVisible();
});

test("admin sees high after-sales risk for repeat returner", async ({
  page,
}) => {
  const stamp = Date.now();
  const sku = `SKU-RISK-${stamp}`;
  await adminLogin(
    page,
    "owner@afterops.local",
    process.env.DEMO_OWNER_PASSWORD ?? "owner123456",
  );
  let cookies = await page.context().cookies();
  const adminCsrf = cookies.find(
    (cookie) => cookie.name === "afterops_admin_csrf",
  )?.value;
  expect(adminCsrf).toBeTruthy();
  const created = await page.request.post(
    "http://127.0.0.1:3000/api/admin/products",
    {
      headers: { "X-Admin-CSRF-Token": adminCsrf! },
      data: {
        sku,
        title: "E2E 风险画像商品",
        price_cents: 1000,
        initial_qty: 10,
      },
    },
  );
  expect(created.ok()).toBeTruthy();

  const phone = uniquePhone(27);
  await loginCustomer(page, phone);
  cookies = await page.context().cookies();
  const customerCsrf = cookies.find(
    (cookie) => cookie.name === "afterops_csrf",
  )?.value;
  expect(customerCsrf).toBeTruthy();
  const headers = {
    "X-Admin-CSRF-Token": adminCsrf!,
    "X-CSRF-Token": customerCsrf!,
  };
  const returnIds: number[] = [];
  for (let index = 0; index < 3; index += 1) {
    const added = await page.request.post(
      "http://127.0.0.1:3000/api/store/cart/items",
      { headers, data: { sku, qty: 1 } },
    );
    expect(added.ok()).toBeTruthy();
    const checkoutResponse = await page.request.post(
      "http://127.0.0.1:3000/api/store/checkout",
      {
        headers: {
          ...headers,
          "Idempotency-Key": `risk-${stamp}-${index}`,
        },
        data: {
          address: {
            recipient_name: "E2E 风险画像顾客",
            recipient_phone: phone,
            region: "华东",
            city: "上海",
            detail: "E2E 风险画像路 1 号",
          },
        },
      },
    );
    expect(checkoutResponse.ok()).toBeTruthy();
    const order = (await checkoutResponse.json()) as {
      order_no: string;
      payment_id: number;
    };
    const paid = await page.request.post(
      `http://127.0.0.1:3000/api/store/payments/${order.payment_id}/mock-confirm`,
      { headers },
    );
    expect(paid.ok()).toBeTruthy();
    const trackingNo = `SF-RISK-${stamp}-${index}`;
    const shipped = await page.request.post(
      `http://127.0.0.1:3000/api/admin/orders/${order.order_no}/shipment`,
      { headers, data: { carrier: "顺丰速运", tracking_no: trackingNo } },
    );
    expect(shipped.ok()).toBeTruthy();
    for (const status of ["in_transit", "out_for_delivery", "delivered"]) {
      expect(
        (
          await page.request.post(
            `http://127.0.0.1:3000/api/admin/orders/${order.order_no}/shipment/advance`,
            { headers, data: { status } },
          )
        ).ok(),
      ).toBeTruthy();
    }
    const returnResponse = await page.request.post(
      "http://127.0.0.1:3000/api/store/returns",
      {
        headers,
        data: {
          order_no: order.order_no,
          reason: "E2E 风险画像退货",
        },
      },
    );
    expect(returnResponse.ok()).toBeTruthy();
    const returnBody = (await returnResponse.json()) as { id: number };
    returnIds.push(returnBody.id);
  }

  await adminLogin(
    page,
    "owner@afterops.local",
    process.env.DEMO_OWNER_PASSWORD ?? "owner123456",
  );
  await page.getByTestId("tab-customers").click();
  await page.getByTestId("customer-search").fill(phone);
  await page.getByRole("button", { name: "搜索" }).click();
  const card = page.locator(".proposal-card").filter({ hasText: phone }).first();
  await expect(card).toBeVisible();
  await card.getByRole("button", { name: "查看档案" }).click();
  const risk = page.getByTestId("customer-risk");
  await expect(risk).toContainText("高风险");
  await expect(risk).toContainText("退货 3 次 / 订单 3 单");
  await expect(risk).toContainText("退货率 100%");
  await expect(risk).toContainText("退货 3 次");

  const latestReturnId = returnIds[returnIds.length - 1];
  await page.getByTestId("tab-returns").click();
  await page.getByTestId("return-filter").selectOption("requested");
  const riskReturnCard = page.getByTestId(
    `admin-return-${latestReturnId}`,
  );
  await expect(riskReturnCard).toContainText("高风险顾客");
  await expect(
    riskReturnCard.getByTestId(`admin-return-risk-${latestReturnId}`),
  ).toContainText("退货 3 次");
});

test.describe("mobile", () => {
  test.use({ viewport: { width: 390, height: 844 } });

  test("storefront and chat fit the mobile viewport", async ({ page }) => {
    const phone = uniquePhone(5);
    await loginCustomer(page, phone);
    await expect(
      page.locator('[data-testid^="add-"]').first(),
    ).toBeVisible();
    const noHorizontalOverflow = await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth + 1,
    );
    expect(noHorizontalOverflow).toBe(true);

    await page.getByTestId("chat-toggle").click();
    await expect(page.getByTestId("chat-input")).toBeVisible();
  });
});
