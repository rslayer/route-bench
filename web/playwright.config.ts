import { defineConfig, devices } from "@playwright/test";

/**
 * End-to-end tests (Task 8).
 *
 * These drive the real Next.js app against a real API. The API is started
 * separately (see e2e/README.md) with a seeded session, because the point is to
 * exercise the seam between them — a mocked API would test the mock.
 *
 * The map is the exception: MapLibre needs a WebGL context and network vector
 * tiles, and CI has neither reliably. The tile source is stubbed and the
 * assertions are on layer/source state rather than pixels, which is what we
 * actually care about — that the right features reached the map.
 */
export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: process.env.CI ? "list" : [["list"], ["html", { open: "never" }]],
  timeout: 30_000,
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://localhost:3111",
    trace: "on-first-retry",
    // The default is a mid-size laptop; the map needs room to be meaningful.
    viewport: { width: 1280, height: 900 },
  },
  projects: [
    { name: "chromium", use: { ...devices["Desktop Chrome"] } },
  ],
});
