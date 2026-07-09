import { defineConfig, devices } from "@playwright/test";

// E2E suite for SHIELD. Expects a running stack:
//   - web on http://localhost:3000 (next start or next dev)
//   - api on http://localhost:8000 (uvicorn), proxied by the web app
// Start locally with: docker compose up db redis minio -d, then
// `uvicorn app.main:create_app --factory` from apps/api and
// `pnpm -F web build && pnpm -F web start`.
export default defineConfig({
  testDir: "./e2e",
  timeout: 60_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  retries: process.env.CI ? 1 : 0,
  workers: 1,
  reporter: [["list"], ["html", { open: "never", outputFolder: "e2e-report" }]],
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://localhost:3000",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
});
