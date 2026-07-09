import { expect, request as playwrightRequest, test } from "@playwright/test";
import type { APIRequestContext } from "@playwright/test";
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

/**
 * Sprint 1 user-facing regression coverage for the SHIELD platform.
 *
 * Flows:
 *   C-1  Extraction empty-file honesty  (UI, tech-debt workspace)
 *   C-2  Legacy .xls rejection          (UI accept attr + proxy 415)
 *   B-3  CSF playbook export gate        (pure API, end to end)
 *
 * The specs drive the real local stack (web :3000 -> api :8000). Where a
 * Sprint-1 navigation gap (D-1/D-2, fixed in Sprint 2) means a workspace has
 * no menu link, we reach it with a direct page.goto by service id rather than
 * failing on the missing link.
 */

const API = "http://localhost:8000";
const ADMIN_EMAIL = "admin@kentro.example";
const ADMIN_PASSWORD = "DemoPass!2026";

// Discovered once in beforeAll against the running API so the specs don't
// hard-code seed UUIDs (the seed can be re-run / re-created).
let atlasClientId = "";
let techDebtServiceId = "";

async function adminToken(ctx: APIRequestContext): Promise<string> {
  const res = await ctx.post(`${API}/auth/login`, {
    data: { email: ADMIN_EMAIL, password: ADMIN_PASSWORD },
  });
  expect(res.ok(), `admin login failed: ${res.status()}`).toBeTruthy();
  const body = (await res.json()) as { access_token: string };
  return body.access_token;
}

function authHeaders(token: string, clientId?: string): Record<string, string> {
  const h: Record<string, string> = { Authorization: `Bearer ${token}` };
  if (clientId) h["X-Client-Id"] = clientId;
  return h;
}

test.beforeAll(async () => {
  const ctx = await playwrightRequest.newContext();
  try {
    const token = await adminToken(ctx);

    const clientsRes = await ctx.get(`${API}/admin/clients`, {
      headers: authHeaders(token),
    });
    expect(clientsRes.ok()).toBeTruthy();
    const { clients } = (await clientsRes.json()) as {
      clients: { id: string; legal_name: string }[];
    };
    const atlas = clients.find((c) => /atlas/i.test(c.legal_name));
    expect(atlas, "Atlas client not found - is the demo seeded?").toBeTruthy();
    atlasClientId = atlas!.id;

    const svcRes = await ctx.get(`${API}/admin/services`, {
      headers: authHeaders(token, atlasClientId),
    });
    expect(svcRes.ok()).toBeTruthy();
    const { services } = (await svcRes.json()) as {
      services: { id: string; kind: string }[];
    };
    const techDebt = services.find((s) => s.kind === "tech_debt");
    expect(techDebt, "tech_debt service not found for Atlas").toBeTruthy();
    techDebtServiceId = techDebt!.id;
  } finally {
    await ctx.dispose();
  }
});

/** UI sign-in through the real credentials form, then pin the active tenant. */
async function signInAsAdmin(
  page: import("@playwright/test").Page,
): Promise<void> {
  await page.goto("/sign-in");
  await page.getByLabel(/email/i).fill(ADMIN_EMAIL);
  await page.getByLabel(/password/i).fill(ADMIN_PASSWORD);
  await page.getByRole("button", { name: /sign in/i }).click();
  // The form does a full-page assign to callbackUrl ("/") on success.
  await page.waitForURL((url) => !url.pathname.includes("/sign-in"), {
    timeout: 15_000,
  });
  // Pin the active-client cookie to Atlas so tenant-scoped proxy calls resolve.
  // (The workspace's EnsureActiveClient would also do this, but pre-setting it
  // makes the .xls proxy POST in C-2 deterministic regardless of that timing.)
  const res = await page.request.post("/api/active-client", {
    data: { clientId: atlasClientId },
  });
  expect(res.ok()).toBeTruthy();
}

test("C-1: header-only CSV extraction reports 'No data rows found'", async ({
  page,
}) => {
  await signInAsAdmin(page);

  // D-1/D-2: no menu link to this workspace in Sprint 1 - reach it directly.
  await page.goto(`/admin/services/${techDebtServiceId}/tech-debt`);
  // Wait past EnsureActiveClient's "Opening workspace…" gate.
  await expect(page.getByText(/Upload inventory and extract/i)).toBeVisible({
    timeout: 15_000,
  });

  // A real temp file containing only the header row, no data rows.
  const dir = mkdtempSync(join(tmpdir(), "shield-e2e-"));
  const csvPath = join(dir, "header-only.csv");
  writeFileSync(csvPath, "name,vendor,annual_cost\n");

  // The Dropzone auto-uploads on file selection and, on a successful upload,
  // immediately runs the extraction (there is no separate extract button in
  // this card). The empty-file honesty check surfaces on the extraction call.
  const fileInput = page.locator('input[type="file"]');
  await fileInput.setInputFiles(csvPath);

  const alert = page
    .getByRole("alert")
    .filter({ hasText: /No data rows found/i });
  await expect(alert).toBeVisible({ timeout: 20_000 });
});

test("C-2: legacy .xls is rejected (accept attr + proxy 415)", async ({
  page,
}) => {
  await signInAsAdmin(page);
  await page.goto(`/admin/services/${techDebtServiceId}/tech-debt`);
  await expect(page.getByText(/Upload inventory and extract/i)).toBeVisible({
    timeout: 15_000,
  });

  // The dropzone's accept list must no longer offer legacy .xls. ".xlsx"
  // contains the substring ".xls", so compare token-wise, not by substring.
  const fileInput = page.locator('input[type="file"]');
  const accept = (await fileInput.getAttribute("accept")) ?? "";
  const tokens = accept.split(",").map((t) => t.trim().toLowerCase());
  expect(tokens).not.toContain(".xls");
  expect(tokens).toContain(".xlsx"); // sanity: xlsx is still accepted

  // Direct proxy POST of an .xls (application/vnd.ms-excel) must 415 with the
  // "re-save …as .xlsx" guidance. page.request shares the browser context's
  // cookies (session + active-client), so the proxy forwards a valid tenant.
  const res = await page.request.post("/api/proxy/artifacts", {
    multipart: {
      file: {
        name: "inventory.xls",
        mimeType: "application/vnd.ms-excel",
        buffer: Buffer.from("legacy-ole2-placeholder-bytes"),
      },
    },
  });
  expect(res.status()).toBe(415);
  expect(await res.text()).toContain("re-save the file as .xlsx");
});

test("B-3: CSF playbook export is gated on unscored rows (409)", async ({
  request,
}) => {
  const token = await adminToken(request);
  const headers = authHeaders(token, atlasClientId);

  // Fresh CSF service + assessment so the gate result is deterministic.
  const title = `E2E B-3 export gate ${Date.now()}`;
  const svcRes = await request.post(`${API}/csf/services`, {
    headers,
    data: { kind: "nist_csf", title },
  });
  expect(svcRes.status(), await svcRes.text()).toBe(201);
  const svc = (await svcRes.json()) as { id: string };

  const asmRes = await request.post(
    `${API}/csf/services/${svc.id}/assessments`,
    { headers },
  );
  expect(asmRes.status()).toBe(201);

  // Seed the Working Profile rows - they are in-scope but unscored (scored_at
  // NULL), which is exactly what the export gate must refuse.
  const seedRes = await request.post(
    `${API}/csf/services/${svc.id}/profiles/seed`,
    { headers, data: { tiers: ["high", "moderate", "low"] } },
  );
  expect(seedRes.ok(), await seedRes.text()).toBeTruthy();

  const exportRes = await request.post(
    `${API}/csf/services/${svc.id}/playbook/export`,
    { headers },
  );
  expect(exportRes.status()).toBe(409);
  const body = (await exportRes.json()) as {
    error?: { message?: string };
    detail?: string;
  };
  const message = body.error?.message ?? body.detail ?? "";
  expect(message).toMatch(/unscored/i);
});
