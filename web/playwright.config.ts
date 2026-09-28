import { defineConfig, devices } from "@playwright/test";

// e2e against the BUILT site (npm run build first), served with the Pages
// routing subset of scripts/serve-dist.mjs. The public API is never
// contacted: tests intercept http://127.0.0.1:8000/api/v1/* with
// contract-shaped fixtures (tests/e2e/support.ts).
const PORT = Number(process.env.E2E_PORT ?? 4329);

export default defineConfig({
  testDir: "tests/e2e",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : "list",
  use: {
    baseURL: `http://localhost:${PORT}`,
    trace: "retain-on-failure",
  },
  projects: [
    { name: "desktop", use: { ...devices["Desktop Chrome"] } },
    { name: "mobile", use: { ...devices["Pixel 7"] } },
  ],
  webServer: {
    command: `node scripts/serve-dist.mjs --root dist --port ${PORT}`,
    url: `http://localhost:${PORT}/`,
    reuseExistingServer: !process.env.CI,
  },
});
