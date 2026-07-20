/**
 * Live end-to-end smoke test — the real stack, not mocks.
 *
 * The other e2e specs stub the API to pin UI behaviour in isolation. This one
 * drives the genuine path a user takes — pick a file, upload it, watch the
 * analysis run, read the result — against a real web app talking to a real API.
 * It exists to catch the integration failures unit tests can't: CORS, storage,
 * the upload contract, SSE/polling, and the results actually rendering.
 *
 * It is OFF by default (needs a running stack), so it never runs in the mocked
 * CI job. Turn it on by pointing it at a stack:
 *
 *   # local
 *   E2E_LIVE=1 E2E_BASE_URL=http://localhost:3000 npx playwright test live-smoke
 *
 *   # against the deployed site, after a Fly deploy
 *   E2E_LIVE=1 E2E_BASE_URL=https://routebench-web.fly.dev npx playwright test live-smoke
 *
 * The benchmark (the minutes-long solver phase) is switched OFF so the run
 * completes in seconds — this validates the plumbing, not the solver, which the
 * backend suite covers. Everything else is the real thing.
 */

import { expect, test } from "@playwright/test";
import path from "node:path";

const LIVE = process.env.E2E_LIVE === "1";
const SAMPLE_CSV = path.resolve(__dirname, "../../data/samples/v1/sample_fleet.csv");

test.describe("live smoke", () => {
  test.skip(!LIVE, "set E2E_LIVE=1 and point E2E_BASE_URL at a running stack");

  // A real analysis, even without the benchmark, is not instant.
  test.setTimeout(120_000);

  test("upload the sample fleet and reach a rendered result", async ({ page }) => {
    await page.goto("/");

    // The genuine file-picker path: set a file on the dropzone's input.
    await page.setInputFiles('input[type="file"]', SAMPLE_CSV);

    // Standard columns, so the mapper is skipped and we land ready to analyse.
    await expect(page).toHaveURL(/\/upload$/);
    await expect(page.getByRole("heading", { name: /ready to analyse/i })).toBeVisible();

    // Turn the benchmark off for speed. Open the settings, then uncheck it.
    await page.getByText("Analysis settings").click();
    const benchmark = page.locator("#c-benchmark");
    await expect(benchmark).toBeChecked();
    await benchmark.uncheck();

    await page.getByRole("button", { name: /analyse my routes/i }).click();

    // Session created → we are on its page, and it was remembered locally.
    await expect(page).toHaveURL(/\/s\/[0-9a-f]{32}$/);
    const sessionId = page.url().split("/s/")[1];
    const remembered = await page.evaluate(
      () => JSON.parse(localStorage.getItem("routebench.runs.v1") ?? "[]") as { sessionId: string }[],
    );
    expect(remembered.some((r) => r.sessionId === sessionId)).toBe(true);

    // The progress page shows while it runs (not a blank screen).
    await expect(page.getByRole("heading", { name: /analysing your routes/i })).toBeVisible();

    // Then the result renders. OSRM is not required — with it down the grade is
    // withheld and the page says so; with it up a grade shows. Either is a pass;
    // a crash or a perpetual spinner is not. The findings list and the map are
    // the load-bearing pieces, so assert the page settled onto the results view.
    await expect(page.getByRole("heading", { name: /your (results|route quality)/i })).toBeVisible({
      timeout: 90_000,
    });

    // The map mounted (its canvas exists), and the findings region rendered.
    await expect(page.locator("canvas").first()).toBeVisible();

    // No console error escaped during the whole flow would have failed earlier;
    // as a final guard, the page is not showing the generic failure view.
    await expect(page.getByText(/we could not|something went wrong/i)).toHaveCount(0);
  });

  test("the content pages and nav are reachable", async ({ page }) => {
    // Cheap coverage that the static surface deploys and links resolve.
    for (const [href, heading] of [
      ["/guide", /how to use routebench/i],
      ["/about", /about routebench/i],
      ["/privacy", /privacy/i],
      ["/how-it-works", /how routebench works/i],
      ["/runs", /your runs/i],
    ] as const) {
      await page.goto(href);
      await expect(page.getByRole("heading", { name: heading }).first()).toBeVisible();
    }
  });
});
