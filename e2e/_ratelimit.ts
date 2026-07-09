import { expect } from "@playwright/test";
import type { APIRequestContext, Page } from "@playwright/test";

/**
 * Shared auth-flow helpers that survive the per-IP auth rate limit.
 *
 * The API rate-limits /auth/login, /auth/register and /auth/refresh at 10/min
 * per IP (SHIELD_RATE_LIMIT_AUTH_PER_MIN). The whole Playwright suite runs from
 * a single IP (127.0.0.1) with workers=1, so across smoke+sprint1+sprint2+
 * sprint3 the cumulative sign-ins sit right at that ceiling and an unlucky burst
 * returns 429. These helpers retry through a 429 (the token bucket refills ~1
 * token / 6s), so a transient rate-limit blip self-heals instead of failing a
 * test. This file is a plain module (no `spec`/`test` in the name) so Playwright
 * does not collect it as a test file.
 */

const sleep = (ms: number): Promise<void> =>
  new Promise((resolve) => setTimeout(resolve, ms));

// Enough to let the auth token bucket refill at least one token between tries
// (the limiter refills ~1 token / 6s). Kept small enough that the worst case
// (3 * 10s waitForURL + 2 * 6s sleep = 42s) still fits a 60s test timeout.
const RETRY_WAIT_MS = 6_000;
const MAX_ATTEMPTS = 3;
const NAV_TIMEOUT_MS = 10_000;

/**
 * Run a UI auth submit (sign-in or sign-up) and wait for the success
 * navigation, retrying through a rate-limit stall. `submit` must (re)fill and
 * submit the form; `success` is the post-success URL predicate.
 */
export async function uiAuthWithRetry(
  page: Page,
  submit: () => Promise<void>,
  success: (url: URL) => boolean,
  label: string,
): Promise<void> {
  let lastErr: unknown;
  for (let attempt = 1; attempt <= MAX_ATTEMPTS; attempt++) {
    await submit();
    try {
      await page.waitForURL(success, { timeout: NAV_TIMEOUT_MS });
      return;
    } catch (err) {
      lastErr = err;
      if (attempt < MAX_ATTEMPTS) await sleep(RETRY_WAIT_MS);
    }
  }
  throw new Error(
    `${label} did not complete after ${MAX_ATTEMPTS} attempts (likely auth ` +
      `rate limit): ${String(lastErr)}`,
  );
}

/** UI sign-in through the real credentials form, resilient to a 429 stall. */
export async function signInResilient(
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

/**
 * Self-register through the /sign-up form, resilient to the shared auth limiter.
 *
 * The sign-up flow is two auth calls: POST /auth/register (create) then an auto
 * POST /auth/login (sign in) that redirects to /intake. Under load the register
 * can succeed (201) while the auto-login is rate-limited (429), leaving the user
 * created but NOT signed in — and a naive re-submit then hits 409 "account
 * already exists" forever. So: drive the form until we either land on /intake
 * (fully done) or observe that the account now exists, then finish with a
 * 429-resilient sign-in. `success` matches the post-auth URL (default: any page
 * off /sign-up and /sign-in).
 */
export async function signUpResilient(
  page: Page,
  fields: { fullName: string; email: string; password: string },
): Promise<void> {
  let accountExists = false;
  for (let attempt = 1; attempt <= MAX_ATTEMPTS; attempt++) {
    await page.goto("/sign-up");
    await page.getByLabel(/full name/i).fill(fields.fullName);
    await page.getByLabel(/email/i).fill(fields.email);
    await page.getByLabel(/password/i).fill(fields.password);
    await page.getByRole("button", { name: /create account/i }).click();
    try {
      await page.waitForURL((url) => url.pathname.includes("/intake"), {
        timeout: NAV_TIMEOUT_MS,
      });
      return; // registered AND auto-signed-in
    } catch {
      // Not at /intake. If the account now exists, stop registering and sign in.
      accountExists = await page
        .getByText(/account already exists/i)
        .isVisible()
        .catch(() => false);
      if (accountExists) break;
      if (attempt < MAX_ATTEMPTS) await sleep(RETRY_WAIT_MS);
    }
  }
  // The account exists (created on some attempt) but the auto-login was rate
  // limited; establish the session through the resilient sign-in path.
  await signInResilient(page, fields.email, fields.password);
}

/** API sign-in that retries through a 429, returning the access token. */
export async function loginApiWithRetry(
  ctx: APIRequestContext,
  apiBase: string,
  email: string,
  password: string,
): Promise<string> {
  let last = "";
  for (let attempt = 1; attempt <= MAX_ATTEMPTS + 2; attempt++) {
    const res = await ctx.post(`${apiBase}/auth/login`, {
      data: { email, password },
    });
    if (res.status() === 429) {
      last = `429 ${await res.text()}`;
      await sleep(RETRY_WAIT_MS);
      continue;
    }
    expect(
      res.ok(),
      `api login failed (${res.status()}): ${await res.text()}`,
    ).toBeTruthy();
    return ((await res.json()) as { access_token: string }).access_token;
  }
  throw new Error(`api login rate-limited after retries: ${last}`);
}
