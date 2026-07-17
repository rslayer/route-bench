import { expect, test } from "@playwright/test";

/**
 * Landing and how-it-works.
 *
 * These assert the promises the pages make, not their layout: a redesign should
 * not break them, but quietly dropping the disclaimer or the honest
 * "not supported" list should.
 */

test.describe("landing", () => {
  test("the promise and the CTA are above the fold", async ({ page }) => {
    await page.goto("/");

    await expect(
      page.getByRole("heading", { name: /benchmark your last-mile routes/i }),
    ).toBeVisible();

    // The dropzone IS the primary CTA — not a link to one.
    const dropzone = page.getByRole("button", { name: /drop your route plan/i });
    await expect(dropzone).toBeVisible();

    // "Above the fold" is the actual claim, so measure it rather than trust it.
    const box = await dropzone.boundingBox();
    const viewport = page.viewportSize();
    expect(box).not.toBeNull();
    expect(box!.y).toBeLessThan(viewport!.height);
  });

  test("the score preview shows a real grade", async ({ page }) => {
    await page.goto("/");
    // Sample fleet, graded under the committed rubric.
    await expect(page.getByText(/68\.8|67\.5/)).toBeVisible();
    await expect(page.getByText("Sequencing Efficiency")).toBeVisible();
    await expect(page.getByText("Density & Territory")).toBeVisible();
  });

  test("the CSV template downloads and matches the schema", async ({ page }) => {
    await page.goto("/");
    const download = page.waitForEvent("download");
    await page.getByRole("link", { name: /download the template/i }).click();
    const file = await download;
    expect(file.suggestedFilename()).toBe("routebench-template.csv");

    const stream = await file.createReadStream();
    const text = await new Promise<string>((resolve, reject) => {
      let out = "";
      stream.on("data", (c) => (out += c));
      stream.on("end", () => resolve(out));
      stream.on("error", reject);
    });
    const header = text.split("\n")[0]!;
    for (const required of ["route_id", "stop_sequence", "latitude", "longitude"]) {
      expect(header).toContain(required);
    }
    // The depot row is what most first uploads get wrong; the template must show it.
    expect(text).toMatch(/\n[^,]+,0,/);
  });

  test("every page carries the footer", async ({ page }) => {
    for (const path of ["/", "/how-it-works"]) {
      await page.goto(path);
      const footer = page.locator("footer");
      await expect(footer).toContainText("independent benchmarking tool");
      await expect(footer).toContainText("Built by Ali Kamil");
      await expect(footer.getByRole("link", { name: /ali@alikamil\.com/ })).toBeVisible();
      await expect(footer.getByRole("link", { name: /source & methodology/i })).toHaveAttribute(
        "href",
        /github\.com\/rslayer\/route-bench/,
      );
      await expect(footer).toContainText("FSL-1.1-ALv2");
    }
  });
});

test.describe("how it works", () => {
  const DISCLAIMER =
    "Benchmark solutions are the best found within a fixed compute budget using metaheuristic optimization; they are not proven mathematical optima. Reported savings are therefore conservative. Travel times are estimates and do not reflect live traffic. RouteBench findings are analytical aids, not operational routing instructions.";

  test("the solver disclaimer appears verbatim", async ({ page }) => {
    await page.goto("/how-it-works");
    // Verbatim is the requirement — paraphrasing a disclaimer defeats it.
    await expect(page.getByText(DISCLAIMER, { exact: true })).toBeVisible();
  });

  test("the honest limits are published", async ({ page }) => {
    await page.goto("/how-it-works");
    await expect(page.getByRole("heading", { name: /what we do not model/i })).toBeVisible();
    for (const limit of [
      "Multiple depots per fleet",
      "Heterogeneous vehicle types",
      "Live or historical traffic data",
      "Planned versus actuals",
    ]) {
      await expect(page.getByRole("rowheader", { name: limit })).toBeVisible();
    }
  });

  test("the rubric is not sold as a peer comparison", async ({ page }) => {
    await page.goto("/how-it-works");
    await expect(page.getByText(/engineering judgment, not a peer percentile/i)).toBeVisible();
  });

  test("both solver engines are described", async ({ page }) => {
    await page.goto("/how-it-works");
    await expect(page.getByRole("heading", { name: /sequencing benchmark \(TSPTW\)/i })).toBeVisible();
    await expect(page.getByRole("heading", { name: /fleet re-optimisation \(VRPTW\)/i })).toBeVisible();
  });
});

test.describe("theme", () => {
  test("light, dark, and system all work and persist", async ({ page }) => {
    await page.emulateMedia({ colorScheme: "light" });
    await page.goto("/");

    const html = page.locator("html");
    await expect(html).toHaveAttribute("data-theme", "light");

    await page.getByRole("radio", { name: "Dark" }).click();
    await expect(html).toHaveAttribute("data-theme", "dark");

    // Persisted across a reload — and applied before paint, so no flash.
    await page.reload();
    await expect(html).toHaveAttribute("data-theme", "dark");

    // System clears the override and follows the OS again.
    await page.getByRole("radio", { name: "System" }).click();
    await expect(html).toHaveAttribute("data-theme", "light");
    await page.reload();
    await expect(html).toHaveAttribute("data-theme", "light");
  });

  test("system follows a dark OS", async ({ page }) => {
    await page.emulateMedia({ colorScheme: "dark" });
    await page.goto("/");
    await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  });
});
