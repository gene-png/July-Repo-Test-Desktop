import { expect, test } from "@playwright/test";
import type { BrowserContext } from "@playwright/test";

import { loginApiWithRetry, signInResilient } from "./_ratelimit";

/**
 * Sprint 3 user-facing regression coverage for the SHIELD platform.
 *
 * Flows driven against the real local stack (web :3000 -> api :8000):
 *   F-3  Risk Register governance gate: generation is gated on APPROVED sources
 *        (an APPROVED ATT&CK mapping + at least one APPROVED CSF/ZT assessment).
 *        The generate -> edit -> lock -> regenerate -> approve -> export loop is a
 *        DOCUMENTED test.fixme (see the block below): it is blocked in the
 *        fixture-mode stack because Risk Register generation calls the LLM and no
 *        `risk_synthesize` fixture is registered at runtime.
 *   D-4a Unauthenticated /assessments redirects to /sign-in with a callbackUrl.
 *   D-4e /dev/questionnaire-preview is admin-gated: a client-role session gets a
 *        404, an admin session renders the renderer preview.
 *   D-4f /admin/active lists in-progress service rows that link into a workspace.
 *   G-1  The client "My assessments" view carries no "Report released" copy; a
 *        terminal card reads "Complete" + the consultant-delivery note.
 *   H-7  /admin/audit renders rows, the action filter narrows them, and the CSV
 *        link responds 200 text/csv.
 *
 * Auth budget: the API rate-limits /auth/login at 10/min per IP and the whole
 * suite shares 127.0.0.1. To stay well under budget this spec mints the admin
 * API token ONCE and captures ONE admin + ONE client browser session
 * (storageState) in beforeAll, then reuses those sessions across the UI tests
 * (no per-test sign-in). The two UI sign-ins go through signInResilient, which
 * retries through a transient 429.
 */

const API = "http://localhost:8000";
const ADMIN_EMAIL = "admin@kentro.example";
const ADMIN_PASSWORD = "DemoPass!2026";
const CLIENT_EMAIL = "client@atlas.example";
const CLIENT_PASSWORD = "DemoPass!2026";

let adminAccessToken = "";
let atlasClientId = "";
// Captured NextAuth sessions, reused per test via browser.newContext().
let adminStorage: Awaited<ReturnType<BrowserContext["storageState"]>>;
let clientStorage: Awaited<ReturnType<BrowserContext["storageState"]>>;

function authHeaders(token: string, clientId?: string): Record<string, string> {
  const h: Record<string, string> = { Authorization: `Bearer ${token}` };
  if (clientId) h["X-Client-Id"] = clientId;
  return h;
}

test.beforeAll(async ({ browser }) => {
  // Two UI sign-ins here (admin + client), each 429-resilient with backoff, so
  // give the hook headroom beyond the default per-test timeout.
  test.setTimeout(180_000);
  const apiCtx = await browser.newContext();
  try {
    adminAccessToken = await loginApiWithRetry(
      apiCtx.request,
      API,
      ADMIN_EMAIL,
      ADMIN_PASSWORD,
    );

    const clientsRes = await apiCtx.request.get(`${API}/admin/clients`, {
      headers: authHeaders(adminAccessToken),
    });
    expect(clientsRes.ok()).toBeTruthy();
    const { clients } = (await clientsRes.json()) as {
      clients: { id: string; legal_name: string }[];
    };
    const atlas = clients.find((c) => /atlas/i.test(c.legal_name));
    expect(atlas, "Atlas client not found - is the demo seeded?").toBeTruthy();
    atlasClientId = atlas!.id;
  } finally {
    await apiCtx.close();
  }

  // Capture one admin session + one client session for reuse.
  const adminCtx = await browser.newContext();
  const adminPage = await adminCtx.newPage();
  await signInResilient(adminPage, ADMIN_EMAIL, ADMIN_PASSWORD);
  adminStorage = await adminCtx.storageState();
  await adminCtx.close();

  const clientCtx = await browser.newContext();
  const clientPage = await clientCtx.newPage();
  await signInResilient(clientPage, CLIENT_EMAIL, CLIENT_PASSWORD);
  clientStorage = await clientCtx.storageState();
  await clientCtx.close();
});

// ---------------------------------------------------------------------------
// F-3: Risk Register generation is gated on APPROVED sources.
//
// A brand-new client has no approved sources -> the gate is locked, its
// `missing` names both requirements, and POST generate returns 409 (never a
// silent generate). Approving a fresh ATT&CK mapping + a fresh CSF assessment
// (the two approve endpoints the fix added) flips the gate to unlocked and marks
// each source `approved`. This is the load-bearing half of F-3 and runs end to
// end against the real API.
// ---------------------------------------------------------------------------
test("F-3: risk gate stays locked until an ATT&CK + CSF/ZT source is APPROVED", async ({
  request,
}) => {
  const h = authHeaders(adminAccessToken);

  // Fresh client so the gate state is deterministic (unaffected by seed / other
  // tests mutating Atlas).
  const cRes = await request.post(`${API}/admin/clients`, {
    headers: h,
    data: { legal_name: `QA F3 Client ${Date.now()}` },
  });
  expect(cRes.status(), await cRes.text()).toBe(201);
  const cid = ((await cRes.json()) as { id: string }).id;
  const th = authHeaders(adminAccessToken, cid);

  // Locked: no sources at all.
  const g0 = await request.get(`${API}/risk/clients/${cid}/gate`, {
    headers: h,
  });
  expect(g0.ok()).toBeTruthy();
  const gate0 = (await g0.json()) as {
    unlocked: boolean;
    missing: string[];
    sources: unknown[];
  };
  expect(gate0.unlocked).toBe(false);
  expect(gate0.missing.length).toBe(2);
  expect(gate0.sources).toHaveLength(0);

  // A locked gate refuses generation with 409 (not a 500, not a silent create).
  const genLocked = await request.post(
    `${API}/risk/clients/${cid}/register/generate`,
    { headers: h },
  );
  expect(genLocked.status(), await genLocked.text()).toBe(409);
  expect(await genLocked.text()).toMatch(/locked/i);

  // Approve a fresh ATT&CK mapping.
  const aSvc = await request.post(`${API}/attack/services`, {
    headers: th,
    data: { title: `QA F3 attack ${Date.now()}` },
  });
  expect(aSvc.status(), await aSvc.text()).toBe(201);
  const aSvcId = ((await aSvc.json()) as { id: string }).id;
  const aAsm = await request.post(
    `${API}/attack/services/${aSvcId}/assessments`,
    { headers: th, data: {} },
  );
  expect(aAsm.status()).toBe(201);
  const aAsmId = ((await aAsm.json()) as { id: string }).id;
  const aAppr = await request.post(
    `${API}/attack/assessments/${aAsmId}/approve`,
    { headers: th },
  );
  expect(aAppr.status(), await aAppr.text()).toBe(200);

  // Still locked: ATT&CK alone is not enough.
  const g1 = await request.get(`${API}/risk/clients/${cid}/gate`, {
    headers: h,
  });
  const gate1 = (await g1.json()) as { unlocked: boolean; has_attack: boolean };
  expect(gate1.has_attack).toBe(true);
  expect(gate1.unlocked).toBe(false);

  // Approve a fresh CSF assessment -> the second requirement is satisfied.
  const cSvc = await request.post(`${API}/csf/services`, {
    headers: th,
    data: { kind: "nist_csf", title: `QA F3 csf ${Date.now()}` },
  });
  expect(cSvc.status(), await cSvc.text()).toBe(201);
  const cSvcId = ((await cSvc.json()) as { id: string }).id;
  const cAsm = await request.post(`${API}/csf/services/${cSvcId}/assessments`, {
    headers: th,
  });
  expect(cAsm.status()).toBe(201);
  const cAsmId = ((await cAsm.json()) as { id: string }).id;
  const cAppr = await request.post(`${API}/csf/assessments/${cAsmId}/approve`, {
    headers: th,
  });
  expect(cAppr.status(), await cAppr.text()).toBe(200);

  // Unlocked, with both sources reported APPROVED.
  const g2 = await request.get(`${API}/risk/clients/${cid}/gate`, {
    headers: h,
  });
  const gate2 = (await g2.json()) as {
    unlocked: boolean;
    missing: string[];
    sources: { kind: string; approved: boolean; status: string | null }[];
  };
  expect(gate2.unlocked).toBe(true);
  expect(gate2.missing).toHaveLength(0);
  const attackSrc = gate2.sources.find((s) => s.kind === "attack");
  const csfSrc = gate2.sources.find((s) => s.kind === "csf");
  expect(attackSrc?.approved).toBe(true);
  expect(attackSrc?.status).toBe("approved");
  expect(csfSrc?.approved).toBe(true);
  expect(csfSrc?.status).toBe("approved");
});

// F-3 (generate -> edit -> lock -> regenerate -> approve -> export) is a
// DOCUMENTED SKIP, blocked in the fixture-mode stack for the SAME root cause as
// the sprint2 E-5 pill:
//
// Risk Register generation (POST /risk/clients/{cid}/register/generate) runs the
// `risk_synthesize` AI job through run_job -> LLMClient.invoke -> the provider.
// In fixture mode the running API builds a BARE FixtureProvider
// (LLMClient.from_settings, apps/api/app/ai/llm.py) and nothing registers canned
// answers at startup/seed (fixtures are only registered inside pytest). So the
// provider raises `KeyError: No fixture registered for purpose='risk_synthesize'`
// and generate 500s. Verified against the running API on an UNLOCKED gate
// (correlation ids in /tmp/shield-api.log). Because a register can never be
// created, every downstream hop the F-3 loop needs — PATCH an entry, lock it,
// regenerate (locked-entry-survives-verbatim), approve, export, the
// export-before-approve 409, and the XLSX/DOCX artifact download — has no
// register to act on and cannot be exercised here. The gate-governance half of
// F-3 is covered by the passing test above; the register loop is covered by the
// backend unit suite (apps/api/tests/unit/test_risk_register.py) where fixtures
// are registered. Registering a runtime `risk_synthesize` fixture (or a demo
// default) in the fixture-mode stack would unblock this end to end.
// UNBLOCKED (lead fix): fixture mode now registers input-grounded runtime
// fixtures (app/ai/demo_fixtures.py) and the gate accepts RELEASED sources,
// so the seeded Atlas client (attack=released, zt=released) runs the loop.
test("F-3: generate -> edit -> lock -> regenerate -> approve -> export loop", async ({
  request,
}) => {
  const h = authHeaders(adminAccessToken, atlasClientId);

  const gate = await request.get(`${API}/risk/clients/${atlasClientId}/gate`, {
    headers: h,
  });
  expect(gate.status(), await gate.text()).toBe(200);
  expect(((await gate.json()) as { unlocked: boolean }).unlocked).toBe(true);

  const gen = await request.post(
    `${API}/risk/clients/${atlasClientId}/register/generate`,
    { headers: h },
  );
  expect(gen.status(), await gen.text()).toBe(201);
  const reg = (await gen.json()) as {
    version: number;
    entries: { id: string; title: string }[];
  };
  expect(reg.entries.length).toBeGreaterThan(0);

  // Edit one entry (marker title; forced likelihood/impact) and lock it.
  const marker = `F3 locked entry ${Date.now()}`;
  const patch = await request.patch(
    `${API}/risk/entries/${reg.entries[0].id}`,
    {
      headers: h,
      data: {
        title: marker,
        likelihood: "very_high",
        impact: "catastrophic",
        locked: true,
      },
    },
  );
  expect(patch.status(), await patch.text()).toBe(200);
  const patched = (await patch.json()) as {
    title: string;
    tier: string | null;
    locked: boolean;
  };
  expect(patched.title).toBe(marker);
  expect(patched.tier).toBe("critical"); // always re-derived in code
  expect(patched.locked).toBe(true);

  // Regenerate: the locked entry survives verbatim in the new version.
  const regen = await request.post(
    `${API}/risk/clients/${atlasClientId}/register/generate`,
    { headers: h },
  );
  expect(regen.status(), await regen.text()).toBe(201);
  const reg2 = (await regen.json()) as {
    version: number;
    entries: { title: string; locked: boolean }[];
  };
  expect(reg2.version).toBe(reg.version + 1);
  const survivor = reg2.entries.find((e) => e.title === marker);
  expect(survivor, "locked entry must survive regenerate").toBeTruthy();
  expect(survivor!.locked).toBe(true);

  // Export before approval is refused; after approval it succeeds and the
  // artifacts download.
  const early = await request.post(
    `${API}/risk/clients/${atlasClientId}/register/export`,
    { headers: h },
  );
  expect(early.status()).toBe(409);

  const approve = await request.post(
    `${API}/risk/clients/${atlasClientId}/register/approve`,
    { headers: h },
  );
  expect(approve.status(), await approve.text()).toBe(200);

  const exp = await request.post(
    `${API}/risk/clients/${atlasClientId}/register/export`,
    { headers: h },
  );
  expect(exp.status(), await exp.text()).toBe(200);
  const exported = (await exp.json()) as {
    xlsx_artifact_id?: string | null;
    docx_artifact_id?: string | null;
  };
  const artifactId = exported.xlsx_artifact_id ?? exported.docx_artifact_id;
  expect(artifactId, JSON.stringify(exported)).toBeTruthy();
  const dl = await request.get(`${API}/artifacts/${artifactId}/download`, {
    headers: h,
    maxRedirects: 0,
  });
  expect(
    [200, 302, 307].includes(dl.status()),
    `download status ${dl.status()}`,
  ).toBe(true);
});

// ---------------------------------------------------------------------------
// D-4a: an unauthenticated visit to /assessments is bounced to /sign-in with a
// callbackUrl back to /assessments (server-side redirect, before paint).
// ---------------------------------------------------------------------------
test("D-4a: unauthenticated /assessments redirects to /sign-in with a callbackUrl", async ({
  browser,
}) => {
  // A pristine (signed-out) context.
  const ctx = await browser.newContext();
  try {
    const page = await ctx.newPage();
    await page.goto("/assessments");
    await page.waitForURL(/\/sign-in/, { timeout: 15_000 });
    const url = new URL(page.url());
    expect(url.pathname).toBe("/sign-in");
    expect(url.searchParams.get("callbackUrl")).toBe("/assessments");
    await expect(page.getByLabel(/email/i)).toBeVisible();
  } finally {
    await ctx.close();
  }
});

// ---------------------------------------------------------------------------
// D-4e: /dev/questionnaire-preview is admin-only. A client-role session gets a
// 404 (no hint the route exists); an admin session renders the preview.
// ---------------------------------------------------------------------------
test("D-4e: /dev/questionnaire-preview 404s for a client, renders for an admin", async ({
  browser,
}) => {
  const clientCtx = await browser.newContext({ storageState: clientStorage });
  try {
    const clientPage = await clientCtx.newPage();
    await clientPage.goto("/dev/questionnaire-preview");
    await expect(
      clientPage.getByText(/This page could not be found/i),
    ).toBeVisible({ timeout: 15_000 });
    // The dev-tool chrome must NOT be present for a client.
    await expect(clientPage.getByText(/Dev preview/i)).toHaveCount(0);
  } finally {
    await clientCtx.close();
  }

  const adminCtx = await browser.newContext({ storageState: adminStorage });
  try {
    const adminPage = await adminCtx.newPage();
    await adminPage.goto("/dev/questionnaire-preview");
    await expect(adminPage.getByText(/Dev preview/i)).toBeVisible({
      timeout: 15_000,
    });
    await expect(
      adminPage.getByRole("heading", { name: /Renderer preview/i }),
    ).toBeVisible();
  } finally {
    await adminCtx.close();
  }
});

// ---------------------------------------------------------------------------
// D-4f: /admin/active lists in-progress service rows, each linking into a
// workspace. A freshly created CSF service is in_progress; assert its row and
// its "Open" workspace link resolve.
// ---------------------------------------------------------------------------
test("D-4f: /admin/active lists an in-progress service linking into a workspace", async ({
  browser,
  request,
}) => {
  // Guarantee at least one in-progress row via the API (a bare CSF service is
  // created in_progress).
  const title = `QA D4f active ${Date.now()}`;
  const svcRes = await request.post(`${API}/csf/services`, {
    headers: authHeaders(adminAccessToken, atlasClientId),
    data: { kind: "nist_csf", title },
  });
  expect(svcRes.status(), await svcRes.text()).toBe(201);
  const svcId = ((await svcRes.json()) as { id: string }).id;

  const ctx = await browser.newContext({ storageState: adminStorage });
  try {
    const page = await ctx.newPage();
    await page.goto("/admin/active");

    // The "In progress (N)" card renders with at least one row.
    await expect(page.getByText(/In progress \(\d+\)/i)).toBeVisible({
      timeout: 15_000,
    });

    // Our created service appears as a row; scope to it and follow its workspace
    // link into the CSF workspace.
    const row = page.getByRole("row").filter({ hasText: title });
    await expect(row).toBeVisible({ timeout: 15_000 });
    const openLink = row.getByRole("link", { name: /Open/i });
    await expect(openLink).toHaveAttribute(
      "href",
      `/admin/services/${svcId}/csf`,
    );
    await openLink.click();
    await page.waitForURL(new RegExp(`/admin/services/${svcId}/csf`), {
      timeout: 15_000,
    });
  } finally {
    await ctx.close();
  }
});

// ---------------------------------------------------------------------------
// G-1: the client "My assessments" view no longer says "Report released"
// anywhere; a terminal (approved/released) card reads "Complete" and carries the
// consultant-delivery note.
// ---------------------------------------------------------------------------
test("G-1: client assessments show 'Complete' + delivery note, never 'Report released'", async ({
  browser,
}) => {
  const ctx = await browser.newContext({ storageState: clientStorage });
  try {
    const page = await ctx.newPage();
    await page.goto("/assessments");

    // A seeded, released assessment card is present (terminal state).
    const seededCard = page
      .getByRole("link")
      .filter({ hasText: /Atlas Defense — NIST CSF 2\.0 Assessment/i });
    await expect(seededCard.first()).toBeVisible({ timeout: 15_000 });

    // No client-facing "release" concept survives (G-1 removed the label).
    await expect(page.getByText(/Report released/i)).toHaveCount(0);

    // The terminal card reads "Complete" and shows the delivery note.
    await expect(seededCard.first().getByText(/^Complete$/)).toBeVisible();
    await expect(
      page.getByText(/your consultant will deliver your report/i).first(),
    ).toBeVisible();
  } finally {
    await ctx.close();
  }
});

// ---------------------------------------------------------------------------
// H-7: /admin/audit renders rows, the action filter narrows them, and the CSV
// link responds 200 text/csv.
// ---------------------------------------------------------------------------
test("H-7: /admin/audit renders, the action filter narrows, CSV responds 200 text/csv", async ({
  browser,
}) => {
  const ctx = await browser.newContext({ storageState: adminStorage });
  try {
    const page = await ctx.newPage();

    // Capture the unfiltered total authoritatively from the initial audit fetch
    // (reading the DOM summary races the async refetch).
    const isAudit = (r: { url(): string }): boolean =>
      r.url().includes("/api/proxy/admin/audit");
    const [firstResp] = await Promise.all([
      page.waitForResponse((r) => isAudit(r) && !r.url().includes("action=")),
      page.goto("/admin/audit"),
    ]);
    const totalBefore = ((await firstResp.json()) as { total: number }).total;
    expect(totalBefore).toBeGreaterThan(0);

    // Rows render (the seed + prior flows wrote a healthy audit trail).
    await expect(page.locator("tbody tr").first()).toBeVisible({
      timeout: 15_000,
    });

    // Narrow by an action that always exists (every sign-in writes one) but is
    // not the only action -> the result set shrinks. Read the filtered total
    // from the API response the Apply triggers.
    await page.getByLabel(/Action/i).fill("user.login");
    const [filtResp] = await Promise.all([
      page.waitForResponse(
        (r) => isAudit(r) && r.url().includes("action=user.login"),
      ),
      page.getByRole("button", { name: /Apply filters/i }).click(),
    ]);
    const totalAfter = ((await filtResp.json()) as { total: number }).total;
    expect(totalAfter).toBeGreaterThan(0);
    expect(totalAfter).toBeLessThan(totalBefore);

    // Every rendered row shows the filtered action (Action is the 2nd column).
    // toHaveText auto-retries until the filtered rows have painted.
    const actionCells = page.locator("tbody tr td:nth-child(2)");
    await expect(actionCells.first()).toHaveText("user.login");
    const count = await actionCells.count();
    for (let i = 0; i < count; i++) {
      await expect(actionCells.nth(i)).toHaveText("user.login");
    }

    // The CSV link responds 200 text/csv. page.request shares the context's
    // session cookie, so the proxy forwards a valid bearer.
    const csvHref = await page
      .getByRole("link", { name: /Download CSV/i })
      .getAttribute("href");
    expect(csvHref, "Download CSV link must have an href").toBeTruthy();
    const csvRes = await page.request.get(csvHref!);
    expect(csvRes.status()).toBe(200);
    expect(csvRes.headers()["content-type"]).toContain("text/csv");
  } finally {
    await ctx.close();
  }
});
