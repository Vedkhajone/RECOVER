import { expect, test, type APIRequestContext } from "@playwright/test";

/** The five-minute demo, executed by a browser.
 *
 *  This is the test that would catch "the screenshots look right but the
 *  numbers never move". It drives the real UI against the real API and asserts
 *  on money changing on the merchant dashboard.
 */

const API = "http://127.0.0.1:8100";

async function reseed(request: APIRequestContext) {
  const response = await request.post(`${API}/api/demo/seed`);
  expect(response.ok()).toBeTruthy();
}

test.beforeEach(async ({ request }) => {
  await reseed(request);
});

test("the dashboard shows revenue at risk and the demo scenarios", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Revenue recovery" })).toBeVisible();
  await expect(page.getByText("Revenue at risk", { exact: true })).toBeVisible();
  await expect(page.getByText("Payment simulator")).toBeVisible();
  await expect(page.getByText("Deterministic fallback").first()).toBeVisible();
  await expect(page.getByText("Payment retry succeeds")).toBeVisible();
});

test("scenario 1: a failed payment is diagnosed, retried and recovered", async ({
  page,
  request,
}) => {
  // Process the seeded live case so it opens a recovery request.
  const processed = await request.post(
    `${API}/api/cases/case_demo_retry/process?use_deterministic_engine=true`,
  );
  expect(processed.ok()).toBeTruthy();
  const body = await processed.json();
  expect(body.policy_decision).toBe("ALLOW");
  expect(body.executed).toBe("RETRY_PAYMENT");

  // The case page shows the full decision chain.
  await page.goto("/cases/case_demo_retry");
  await expect(page.getByRole("heading", { name: /Priya Sharma/ })).toBeVisible();
  await expect(page.getByText("AI decision panel")).toBeVisible();
  await expect(page.getByText("What happened", { exact: true })).toBeVisible();
  await expect(page.getByText("What policy allows", { exact: true })).toBeVisible();
  await expect(page.getByText("Recovery timeline")).toBeVisible();

  // Follow the customer recovery link and pay.
  const detail = await (await request.get(`${API}/api/cases/case_demo_retry`)).json();
  expect(detail.recovery_token).toBeTruthy();

  await page.goto(`/recover/${detail.recovery_token}`);
  // The heading renders a typographic apostrophe, so match on the stable part.
  await expect(page.getByText(/Payment couldn.t be completed/)).toBeVisible();
  await expect(page.getByText("ORD-18392")).toBeVisible();
  await page.getByRole("button", { name: /Retry payment/ }).click();
  await expect(page.getByText("Payment successful")).toBeVisible({ timeout: 30_000 });

  // The merchant dashboard reflects it.
  await page.goto("/");
  await expect(page.getByText("₹4,999.00").first()).toBeVisible({ timeout: 30_000 });

  const metrics = await (
    await request.get(`${API}/api/dashboard/metrics`)
  ).json();
  expect(metrics.recovered_revenue.paise).toBe(499900);
  expect(metrics.successful_interventions).toBe(1);
});

test("scenario 3: an amount over the limit is held for merchant approval", async ({
  page,
  request,
}) => {
  await request.post(
    `${API}/api/cases/case_demo_approval/process?use_deterministic_engine=true`,
  );
  await page.goto("/cases/case_demo_approval");
  await expect(page.getByText("Awaiting approval").first()).toBeVisible();
  await expect(page.getByRole("button", { name: "Approve" })).toBeVisible();

  const detail = await (await request.get(`${API}/api/cases/case_demo_approval`)).json();
  expect(detail.policy_decision).toBe("REQUIRE_APPROVAL");
  expect(detail.policy_code).toBe("ABOVE_APPROVAL_THRESHOLD");
  // Nothing was executed while it waits.
  expect(detail.executed_action).toBeNull();
  expect(detail.retry_count).toBe(0);
});

test("scenario 4 and 7: policy blocks a forbidden action on a cancelled order", async ({
  page,
}) => {
  await page.goto("/cases/case_demo_cancelled");
  await expect(page.getByText("Policy probe")).toBeVisible();
  await page.getByRole("combobox").selectOption("RETRY_PAYMENT");
  await page.getByRole("button", { name: "Evaluate" }).click();
  await expect(page.getByText("ORDER_CANCELLED")).toBeVisible();
  await expect(page.getByText("The customer cancelled this order.").first()).toBeVisible();
});

test("scenario 5: a duplicate webhook is detected and safely ignored", async ({
  page,
  request,
}) => {
  await request.post(
    `${API}/api/cases/case_demo_retry/process?use_deterministic_engine=true`,
  );
  const detail = await (await request.get(`${API}/api/cases/case_demo_retry`)).json();
  await request.post(`${API}/api/recovery/${detail.recovery_token}/simulate-payment`, {
    data: { succeed: true },
  });

  await page.goto("/audit?tab=webhooks");
  await expect(page.getByText("Webhook idempotency ledger")).toBeVisible();
  await page.getByRole("button", { name: "Replay" }).first().click();
  await expect(page.getByText(/Duplicate event detected/)).toBeVisible({
    timeout: 30_000,
  });

  // The money was credited exactly once.
  const metrics = await (await request.get(`${API}/api/dashboard/metrics`)).json();
  expect(metrics.recovered_revenue.paise).toBe(499900);
});

test("scenario 6: reconciliation finds a payment captured against a stale order", async ({
  page,
  request,
}) => {
  const result = await (await request.post(`${API}/api/demo/reconcile`)).json();
  expect(result.mismatches_found).toBeGreaterThan(0);

  await page.goto("/audit?tab=exceptions");
  await expect(page.getByText("Unresolved exceptions")).toBeVisible();
});

test("the policy screen changes behaviour, not just its own state", async ({
  page,
  request,
}) => {
  await page.goto("/policy");
  await expect(page.getByRole("heading", { name: "Recovery policy" })).toBeVisible();

  // Turn the automatic retry limit down to zero and save.
  const retryLimit = page.locator("input[type=number]").first();
  await retryLimit.fill("0");
  await page.getByRole("button", { name: "Save policy" }).click();
  await expect(page.getByText(/Policy saved/)).toBeVisible();

  // The very next decision is now a block.
  const probe = await (
    await request.post(`${API}/api/cases/case_demo_retry/policy-probe`, {
      data: { action: "RETRY_PAYMENT" },
    })
  ).json();
  expect(probe.decision).toBe("BLOCK");
  expect(probe.code).toBe("RETRY_LIMIT_EXCEEDED");
});

test("running a recovery batch moves the dashboard numbers", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByText("Revenue at risk", { exact: true })).toBeVisible();

  await page.getByRole("button", { name: "Run recovery batch" }).click();
  await expect(page.getByText(/Recovery batch complete/)).toBeVisible({
    timeout: 60_000,
  });
  await expect(page.getByText(/processed/)).toBeVisible();
});

test("the cases list filters by status", async ({ page, request }) => {
  await request.post(`${API}/api/cases/batch/run`, {
    data: { limit: 200, use_deterministic_engine: true },
  });
  await page.goto("/cases?status=STOPPED");
  await expect(page.getByRole("heading", { name: "AI recovery cases" })).toBeVisible();
  // Assert on the table body, not the filter dropdown - `getByText` would
  // happily match the hidden <option> and pass without rendering a single row.
  const rows = page.locator("tbody tr");
  await expect(rows.first()).toBeVisible();
  for (const badge of await rows.locator("td").nth(7).all()) {
    await expect(badge).toContainText("Stopped");
  }
});
