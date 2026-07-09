import { expect, request as playwrightRequest, test } from "@playwright/test";
import type { APIRequestContext, Page } from "@playwright/test";

import {
  loginApiWithRetry,
  signUpResilient,
  uiAuthWithRetry,
} from "./_ratelimit";

/**
 * Sprint 2 user-facing regression coverage for the SHIELD platform.
 *
 * Flows driven against the real local stack (web :3000 -> api :8000):
 *   D-1  Assessment cards link + admin reply readable in the thread; /messages
 *        is a real inbox with thread rows linking onward.
 *   D-2  Admin ClientSwitcher: no-client empty state renders an inline picker;
 *        picking a client loads the risk-register gate/dashboard.
 *   E-5  "simulated" badge wiring + admin AI status banner copy in fixture mode.
 *   D-3  Incomplete-intake create surfaces the API's typed message (not the raw
 *        "Intake proxy 422") plus a "Finish intake" link.
 *   H-6  run-ai?preview=1 returns the redacted payload without writing an
 *        llm_calls row (usage does not increase).
 *
 * NOTE: the auth endpoints are rate limited (10/min per IP). To stay under the
 * budget the admin API token is minted ONCE in beforeAll and reused; UI sign-ins
 * (unavoidably one /auth/login each) are spread across slow browser tests.
 */

const API = "http://localhost:8000";
const ADMIN_EMAIL = "admin@kentro.example";
const ADMIN_PASSWORD = "DemoPass!2026";
const CLIENT_EMAIL = "client@atlas.example";
const CLIENT_PASSWORD = "DemoPass!2026";

// Discovered once against the running API so the specs don't hard-code seed
// UUIDs (the seed can be re-run / re-created).
let adminAccessToken = "";
let atlasClientId = "";
let seededCsfServiceId = "";
let seededCsfTitle = "";
let techDebtServiceId = "";

function authHeaders(token: string, clientId?: string): Record<string, string> {
  const h: Record<string, string> = { Authorization: `Bearer ${token}` };
  if (clientId) h["X-Client-Id"] = clientId;
  return h;
}

async function loginApi(
  ctx: APIRequestContext,
  email: string,
  password: string,
): Promise<string> {
  // Retry through a transient auth 429 (shared per-IP limiter, Sprint 3).
  return loginApiWithRetry(ctx, API, email, password);
}

/** UI sign-in through the real credentials form (429-resilient). */
async function signIn(
  page: Page,
  email: string,
  password: string,
): Promise<void> {
  await uiAuthWithRetry(
    page,
    async () => {
      await page.goto("/sign-in");
      await page.getByLabel(/email/i).fill(email);
      await page.getByLabel(/password/i).fill(password);
      await page.getByRole("button", { name: /sign in/i }).click();
    },
    (url) => !url.pathname.includes("/sign-in"),
    "sign-in",
  );
}

/** Pin the admin's active-client cookie so tenant-scoped proxy calls resolve. */
async function pinActiveClient(page: Page, clientId: string): Promise<void> {
  const res = await page.request.post("/api/active-client", {
    data: { clientId },
  });
  expect(res.ok()).toBeTruthy();
}

test.beforeAll(async () => {
  const ctx = await playwrightRequest.newContext();
  try {
    adminAccessToken = await loginApi(ctx, ADMIN_EMAIL, ADMIN_PASSWORD);

    const clientsRes = await ctx.get(`${API}/admin/clients`, {
      headers: authHeaders(adminAccessToken),
    });
    expect(clientsRes.ok()).toBeTruthy();
    const { clients } = (await clientsRes.json()) as {
      clients: { id: string; legal_name: string }[];
    };
    const atlas = clients.find((c) => /atlas/i.test(c.legal_name));
    expect(atlas, "Atlas client not found - is the demo seeded?").toBeTruthy();
    atlasClientId = atlas!.id;

    const svcRes = await ctx.get(`${API}/admin/services`, {
      headers: authHeaders(adminAccessToken, atlasClientId),
    });
    expect(svcRes.ok()).toBeTruthy();
    const { services } = (await svcRes.json()) as {
      services: { id: string; kind: string; title: string }[];
    };
    const csf = services.find(
      (s) =>
        s.kind === "nist_csf" &&
        /atlas/i.test(s.title) &&
        /NIST CSF/i.test(s.title),
    );
    expect(csf, "seeded Atlas NIST CSF service not found").toBeTruthy();
    seededCsfServiceId = csf!.id;
    seededCsfTitle = csf!.title;

    const techDebt = services.find((s) => s.kind === "tech_debt");
    expect(techDebt, "tech_debt service not found for Atlas").toBeTruthy();
    techDebtServiceId = techDebt!.id;
  } finally {
    await ctx.dispose();
  }
});

// ---------------------------------------------------------------------------
// D-1: assessment cards link + admin reply readable in the thread; inbox rows.
// ---------------------------------------------------------------------------
test("D-1: client reads the admin reply by clicking from /assessments; /messages is an inbox", async ({
  page,
}) => {
  // Admin posts a unique reply into the seeded CSF service thread (API; admin
  // must send X-Client-Id). Reuse the beforeAll token to spare the rate limit.
  const marker = `D1 admin reply ${Date.now()}`;
  const post = await page.request.post(
    `${API}/services/${seededCsfServiceId}/messages`,
    {
      headers: authHeaders(adminAccessToken, atlasClientId),
      data: { body: marker },
    },
  );
  expect(post.status(), await post.text()).toBe(201);

  await signIn(page, CLIENT_EMAIL, CLIENT_PASSWORD);

  // /messages renders an inbox with at least one thread row linking onward.
  await page.goto("/messages");
  const inboxLink = page.getByRole("link").filter({ hasText: seededCsfTitle });
  await expect(inboxLink.first()).toBeVisible({ timeout: 15_000 });
  await expect(inboxLink.first()).toHaveAttribute(
    "href",
    new RegExp(`/self-assessment/${seededCsfServiceId}`),
  );

  // D-1 click path: from /assessments, click the card (real navigation - no
  // page.goto for THIS hop; the card link must exist per D-1).
  await page.goto("/assessments");
  const card = page.getByRole("link").filter({ hasText: seededCsfTitle });
  await expect(card.first()).toBeVisible({ timeout: 15_000 });
  await card.first().click();
  await page.waitForURL(new RegExp(`/self-assessment/${seededCsfServiceId}`), {
    timeout: 15_000,
  });

  // The admin reply is readable in the thread (author rendered as "SHIELD analyst").
  await expect(page.getByText(marker)).toBeVisible({ timeout: 15_000 });
  await expect(page.getByText(/SHIELD analyst/i).first()).toBeVisible();
});

// ---------------------------------------------------------------------------
// D-2: admin ClientSwitcher - no-client empty state picker loads the register.
// ---------------------------------------------------------------------------
test("D-2: risk-register no-client empty state renders an inline picker that loads the dashboard", async ({
  page,
}) => {
  await signIn(page, ADMIN_EMAIL, ADMIN_PASSWORD);

  // Ensure no active-client cookie is set (a prior flow may have pinned one).
  const clear = await page.request.post("/api/active-client", {
    data: { clientId: null },
  });
  expect(clear.ok()).toBeTruthy();

  await page.goto("/admin/risk-register");

  // Empty state + inline picker (scoped to the empty state, not the header one).
  await expect(page.getByText(/Pick a client first/i)).toBeVisible({
    timeout: 15_000,
  });
  const inlinePicker = page
    .getByText(/Pick a client:/i)
    .locator("xpath=..")
    .getByLabel(/Active client/i);
  await expect(inlinePicker).toBeVisible();

  // Pick Atlas through the UI. selectOption waits for the option to hydrate.
  await inlinePicker.selectOption(atlasClientId);

  // Selecting a client resolves the dead end (D-2's actual promise): either
  // the dashboard heading renders (gate unlocked) or the gate's locked state
  // for the chosen client renders. Both prove the inline picker worked.
  await expect(
    page
      .getByRole("heading", { name: /Risk Register/i })
      .or(page.getByText(/Risk Register is locked/i)),
  ).toBeVisible({ timeout: 15_000 });
  await expect(page.getByText(/Pick a client first/i)).toHaveCount(0);
});

// ---------------------------------------------------------------------------
// E-5: simulated badge wiring + admin AI status banner copy in fixture mode.
// ---------------------------------------------------------------------------
test("E-5: admin AI status banner shows the 'simulated (deterministic fixtures)' copy", async ({
  page,
}) => {
  await signIn(page, ADMIN_EMAIL, ADMIN_PASSWORD);
  await pinActiveClient(page, atlasClientId);

  // The AiStatusBanner is mounted on the tech-debt workspace and renders the
  // fixture-mode warning copy (E-5 rewrite).
  await page.goto(`/admin/services/${techDebtServiceId}/tech-debt`);
  await expect(page.getByText(/Upload inventory and extract/i)).toBeVisible({
    timeout: 15_000,
  });

  const banner = page.getByRole("status").filter({ hasText: /simulated/i });
  await expect(banner).toBeVisible({ timeout: 15_000 });
  await expect(banner).toContainText(/deterministic fixtures/i);
  await expect(banner).toContainText(/SHIELD_LLM_MODE=live/i);
});

// E-5 (badge-after-run) is a DOCUMENTED SKIP - genuinely blocked in the live
// stack. The SimulatedBadge is wired in ZtWorkspace/AttackWorkspace/
// CsfPlaybookPanel as `runResult.mode === "fixture" ? <SimulatedBadge/> : null`,
// so surfacing it needs a SUCCESSFUL run-ai whose response carries mode. But in
// fixture mode the running API's FixtureProvider (apps/api/app/ai/llm.py) ships
// with NO registered fixtures and nothing registers canned answers at
// startup/seed (routes' _llm_dep -> LLMClient.from_settings() builds a bare
// provider; seed_demo.py writes scores directly, never via run-ai). So every
// non-preview run-ai 500s with `KeyError: No fixture registered for
// purpose='csf_score'` (verified via the API and /tmp/shield-api.log). A live
// run that would show the badge cannot be produced without changing app code.
// The banner half of E-5 is covered by the passing test above.
//
// Sprint 3 re-verification: still blocked. The same bare-FixtureProvider root
// cause was reconfirmed against the fresh build via the Risk Register generate
// path — POST /risk/.../register/generate on an UNLOCKED gate 500s with
// `KeyError: No fixture registered for purpose='risk_synthesize'`. Every AI
// purpose (csf_score / zt_score / mitre_map / risk_synthesize) shares that
// empty-provider fate, so no successful run-ai can surface the pill here. See
// the F-3 register-loop fixme note in sprint3.spec.ts.
// UNBLOCKED (lead fix): fixture mode registers input-grounded runtime
// fixtures (app/ai/demo_fixtures.py), so Run AI succeeds and the pill renders.
test("E-5: 'simulated' pill appears next to a successful Run AI result", async ({
  page,
}) => {
  // The seeded Atlas CSF assessment is RELEASED (read-only workspace), so
  // create a fresh CSF service + draft assessment to run AI against.
  const svcRes = await page.request.post(`${API}/csf/services`, {
    headers: authHeaders(adminAccessToken, atlasClientId),
    data: { kind: "nist_csf", title: `E2E E-5 pill ${Date.now()}` },
  });
  expect(svcRes.status(), await svcRes.text()).toBe(201);
  const freshServiceId = ((await svcRes.json()) as { id: string }).id;
  const aRes = await page.request.post(
    `${API}/csf/services/${freshServiceId}/assessments`,
    { headers: authHeaders(adminAccessToken, atlasClientId) },
  );
  expect([200, 201].includes(aRes.status()), await aRes.text()).toBe(true);

  await signIn(page, ADMIN_EMAIL, ADMIN_PASSWORD);
  await pinActiveClient(page, atlasClientId);

  await page.goto(`/admin/services/${freshServiceId}/csf`);
  const runButton = page.getByRole("button", { name: /Run AI \(csf_score\)/i });
  await expect(runButton).toBeVisible({ timeout: 15_000 });
  await expect(runButton).toBeEnabled({ timeout: 15_000 });
  await runButton.click();

  // Fixture-mode run completes and the result line carries the pill.
  await expect(page.getByText(/simulated/i).first()).toBeVisible({
    timeout: 30_000,
  });
});

// ---------------------------------------------------------------------------
// H-6: run-ai?preview=1 returns the redacted payload without writing an
// llm_calls row (per-client AI usage does not increase).
// ---------------------------------------------------------------------------
test("H-6: run-ai preview returns the redacted payload and writes no llm_calls row", async ({
  request,
}) => {
  const headers = authHeaders(adminAccessToken, atlasClientId);

  // Fresh CSF service + assessment so preview has real inputs to redact.
  const svcRes = await request.post(`${API}/csf/services`, {
    headers,
    data: { kind: "nist_csf", title: `E2E H-6 preview ${Date.now()}` },
  });
  expect(svcRes.status(), await svcRes.text()).toBe(201);
  const svc = (await svcRes.json()) as { id: string };
  const asmRes = await request.post(
    `${API}/csf/services/${svc.id}/assessments`,
    {
      headers,
    },
  );
  expect(asmRes.status()).toBe(201);

  const usageBefore = await atlasCalls(request);

  const prev = await request.post(
    `${API}/csf/services/${svc.id}/run-ai?preview=1`,
    { headers },
  );
  expect(prev.status(), await prev.text()).toBe(200);
  const body = (await prev.json()) as {
    preview?: boolean;
    redacted_payload?: unknown;
    redaction_summary?: unknown;
  };
  expect(body.preview).toBe(true);
  expect(
    body.redacted_payload,
    "preview must carry a redacted payload",
  ).toBeDefined();
  expect(
    body.redaction_summary,
    "preview must carry a redaction summary",
  ).toBeDefined();

  const usageAfter = await atlasCalls(request);
  expect(usageAfter, "a preview run must not write an llm_calls row").toBe(
    usageBefore,
  );
});

// ---------------------------------------------------------------------------
// D-3: incomplete-intake create surfaces the typed API message + Finish-intake
// link (rather than the raw "Intake proxy 422").
//
// A fresh self-registration always auto-populates legal_name (from the work
// domain or the display name), so it does NOT hit the intake guard. The only
// tenant that reaches the guard is one whose legal_name is still "(pending
// intake)". We reproduce that reliably: admin provisions a "(pending intake)"
// client + approves a fresh domain, then a brand-new user self-registers on
// that domain (auto-joining the pending-intake org) and tries to start an
// assessment before completing intake.
// ---------------------------------------------------------------------------
async function provisionPendingIntakeSignup(
  page: Page,
): Promise<{ email: string; password: string }> {
  // Register + a fallback resilient sign-in can chain several 429 backoffs under
  // full-suite auth load; give the test room beyond the 60s default (Sprint 3).
  test.setTimeout(150_000);
  const ts = Date.now();
  const domain = `qa-pending-${ts}.example`;
  const email = `newuser-${ts}@${domain}`;
  const password = "qa-pending-user-1234";

  const client = await page.request.post(`${API}/admin/clients`, {
    headers: authHeaders(adminAccessToken),
    data: { legal_name: "(pending intake)" },
  });
  expect(client.status(), await client.text()).toBe(201);
  const { id: cid } = (await client.json()) as { id: string };

  const dom = await page.request.post(`${API}/admin/clients/${cid}/domains`, {
    headers: authHeaders(adminAccessToken),
    data: { domain },
  });
  expect(dom.status(), await dom.text()).toBe(201);

  // Self-register through the real sign-up form; on success it signs in and
  // full-page-assigns to /intake. /auth/register + its auto-login share the
  // per-IP auth limiter, so use the 429-resilient sign-up (which falls back to a
  // resilient sign-in if the post-register auto-login is throttled). Sprint 3.
  await signUpResilient(page, {
    fullName: "Pending Intake User",
    email,
    password,
  });

  return { email, password };
}

/** Drive the /assessments "start a new assessment" form to the create call. */
async function attemptCreateAssessment(page: Page): Promise<void> {
  await page.goto("/assessments");
  await page
    .getByRole("button", { name: /\+ Start a new assessment/i })
    .click();
  await page.getByLabel(/Target tier/i).selectOption("3");
  await page.getByLabel(/Impact profile/i).selectOption("MOD");
  await page.getByRole("button", { name: /^Start assessment$/i }).click();
}

test("D-3: incomplete-intake create shows the typed API message, not 'Intake proxy 422'", async ({
  page,
}) => {
  await provisionPendingIntakeSignup(page);
  await attemptCreateAssessment(page);

  // p[role=alert] scopes past Next's own route-announcer div[role=alert].
  const alert = page.locator('p[role="alert"]');
  await expect(alert).toBeVisible({ timeout: 15_000 });
  // The typed API detail is surfaced (D-3), not the raw proxy placeholder.
  await expect(alert).toContainText(
    /complete your organization profile in intake/i,
  );
  await expect(alert).not.toContainText(/Intake proxy 422/i);
});

test("D-3: incomplete-intake create shows a 'Finish intake' link to /intake", async ({
  page,
}) => {
  await provisionPendingIntakeSignup(page);
  await attemptCreateAssessment(page);

  await expect(page.locator('p[role="alert"]')).toBeVisible({
    timeout: 15_000,
  });
  const finish = page.getByRole("link", { name: /Finish intake/i });
  await expect(finish).toBeVisible();
  await expect(finish).toHaveAttribute("href", "/intake");
});

/** Total AI call count booked to Atlas in GET /admin/ai-usage. */
async function atlasCalls(ctx: APIRequestContext): Promise<number> {
  const res = await ctx.get(`${API}/admin/ai-usage`, {
    headers: authHeaders(adminAccessToken),
  });
  expect(res.ok()).toBeTruthy();
  const { rows } = (await res.json()) as {
    rows: { client_id: string; calls: number }[];
  };
  return rows
    .filter((r) => r.client_id === atlasClientId)
    .reduce((sum, r) => sum + r.calls, 0);
}
