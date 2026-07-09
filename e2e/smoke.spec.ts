import { test, expect } from "@playwright/test";

// Sprint 0 smoke: the stack is up and the two entry pages render.
// Deeper flow specs are added per sprint next to this file.

test("home page renders the marketing hero", async ({ page }) => {
  await page.goto("/");
  await expect(page.locator("body")).toBeVisible();
  await expect(page).toHaveTitle(/SHIELD/i);
});

test("sign-in page renders the credentials form", async ({ page }) => {
  await page.goto("/sign-in");
  await expect(page.getByLabel(/email/i)).toBeVisible();
  await expect(page.getByLabel(/password/i)).toBeVisible();
});
