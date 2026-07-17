import { expect, test, type Page } from "@playwright/test";

/**
 * The acceptance flow: land → drop a file with non-standard headers → confirm
 * the suggested mapping → settings → submit.
 *
 * The upload POST is intercepted rather than allowed through: a real analysis
 * takes minutes and needs OSRM and an LLM key, and what this test is about is
 * the client's half of the contract — that a messy file becomes a standard one,
 * that the panel state becomes the config, and that both reach the wire intact.
 * The server's half has its own tests in Python, against the real validator.
 */

/** A plausible customer export: none of the headers match RouteBench's. */
const MESSY_CSV = [
  "Route,Seq,Lat,Lng,Dwell Time,TW Open,TW Close,Customer,Notes",
  "RT-9,0,32.7767,-96.7970,0,,,DEPOT,start of day",
  "RT-9,1,32.7850,-96.8050,7,09:00,17:00,ACME-1,ring bell",
  "RT-9,2,32.7920,-96.8100,7,09:00,17:00,ACME-2,side door",
  "RT-9,3,32.7990,-96.8150,7,10:00,15:00,ACME-3,",
].join("\n");

async function dropFile(page: Page, name: string, body: string) {
  await page.getByRole("button", { name: /drop your route plan/i }).click();
  await page.locator('input[type="file"]').setInputFiles({
    name,
    mimeType: "text/csv",
    buffer: Buffer.from(body),
  });
}

/** Capture the multipart POST without letting it reach the worker. */
async function interceptUpload(page: Page) {
  const captured: { file?: string; config?: string } = {};
  await page.route("**/sessions", async (route) => {
    if (route.request().method() !== "POST") return route.continue();
    const data = route.request().postData() ?? "";
    captured.file = data;
    const match = data.match(/name="config"\r?\n\r?\n([\s\S]*?)\r?\n--/);
    captured.config = match?.[1];
    await route.fulfill({
      status: 202,
      contentType: "application/json",
      body: JSON.stringify({ session_id: "e2e0000000000000000000000000001", status_url: "/x" }),
    });
  });
  return captured;
}

test.describe("upload", () => {
  test("a file with non-standard headers is mapped and accepted", async ({ page }) => {
    const captured = await interceptUpload(page);
    await page.goto("/");
    await dropFile(page, "customer-export.csv", MESSY_CSV);

    // The mapper must appear — these headers match nothing.
    await expect(page.getByRole("heading", { name: /check your columns/i })).toBeVisible();

    // And it must have inferred the obvious ones rather than making the user
    // do the work. These are the spec's own examples.
    await expect(page.getByLabel("RouteBench field for Route")).toHaveValue("route_id");
    await expect(page.getByLabel("RouteBench field for Lat")).toHaveValue("latitude");
    await expect(page.getByLabel("RouteBench field for Lng")).toHaveValue("longitude");
    await expect(page.getByLabel("RouteBench field for Seq")).toHaveValue("stop_sequence");
    await expect(page.getByLabel("RouteBench field for TW Open")).toHaveValue(
      "time_window_start",
    );

    // "Notes" has no RouteBench meaning; it must be dropped, not guessed.
    await expect(page.getByLabel("RouteBench field for Notes")).toHaveValue("");

    await page.getByRole("button", { name: /analyse my routes/i }).click();

    // The client rewrites headers before upload, so the server sees the
    // standard schema and never has to know about "Dwell Time".
    await expect
      .poll(() => captured.file, { timeout: 10_000 })
      .toContain("route_id,stop_sequence,latitude,longitude");
    expect(captured.file).not.toContain("Dwell Time");
  });

  test("a standard file skips the mapper", async ({ page }) => {
    await interceptUpload(page);
    await page.goto("/");
    await dropFile(
      page,
      "standard.csv",
      "route_id,stop_sequence,latitude,longitude\nR1,0,32.7,-96.8\nR1,1,32.8,-96.9\n",
    );
    // No mapping needed: the headers already are the schema.
    await expect(page.getByText(/columns match the RouteBench format/i)).toBeVisible();
  });

  test("a non-CSV is refused locally, before any upload", async ({ page }) => {
    await page.goto("/");
    await page.locator('input[type="file"]').setInputFiles({
      name: "plan.xlsx",
      mimeType: "application/vnd.ms-excel",
      buffer: Buffer.from("PK\x03\x04binary"),
    });
    await expect(page.getByRole("alert").filter({ hasText: /not a csv/i })).toBeVisible();
  });

  test("the panel state is exactly the config sent", async ({ page }) => {
    const captured = await interceptUpload(page);
    await page.goto("/");
    await dropFile(page, "standard.csv", MESSY_CSV);
    await expect(page.getByRole("heading", { name: /check your columns/i })).toBeVisible();

    // Change something the user can see, and require it on the wire.
    await page.getByText("Analysis settings", { exact: false }).first().click();
    await page.getByLabel(/delivery time windows/i).uncheck();

    await page.getByRole("button", { name: /analyse my routes/i }).click();

    await expect.poll(() => captured.config, { timeout: 10_000 }).toBeTruthy();
    const config = JSON.parse(captured.config!);
    // The promise is no hidden defaults: what was unchecked is off on the wire.
    expect(config.work_rules.enforce_time_windows).toBe(false);
  });
});
