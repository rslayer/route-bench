import { expect, test, type Page } from "@playwright/test";

/**
 * The results page: score, map, findings, and the loop between them.
 *
 * The API is mocked here so this runs anywhere, including CI with no OSRM and no
 * LLM key. The fixtures below are shaped exactly like the real artifacts — the
 * Python suite guards that shape, so if the contract moves, that side fails
 * first and loudly rather than this one drifting quietly.
 *
 * The map's tiles are stubbed to an empty style: MapLibre needs a WebGL context
 * and network vector tiles, and CI reliably has neither. Assertions are on
 * source and layer state — did the right features reach the map — which is the
 * thing worth testing. Pixels are not.
 */

const SESSION = "e2e0000000000000000000000000001";

const STATUS = {
  session_id: SESSION,
  state: "succeeded",
  progress_pct: 100,
  stage_detail: "Report ready",
  created_at: "2026-07-17T08:00:00Z",
  updated_at: "2026-07-17T08:02:00Z",
  error: null,
  artifacts: {
    report_html: "report.html",
    report_pdf: "report.pdf",
    analysis_json: "analysis.json",
    telemetry_json: "telemetry.json",
    routes_geojson: "routes.geojson",
  },
  cost: null,
};

const FINDING = {
  finding_id: "abc123def456",
  category: "compliance",
  severity: "high",
  confidence: 0.9,
  title: "Route R-001: 2 stop(s) reached after their time window closes",
  evidence: [
    {
      metric_name: "time_window_violations",
      actual_value: 2,
      comparison_value: 0,
      comparison_type: "threshold",
      unit: "stops",
    },
  ],
  references: { route_ids: ["R-001"], stop_sequences: [] },
  hypothesis: "Route R-001 cannot serve 2 stops within their committed windows.",
  suggested_investigation: "Confirm the committed windows, then test resequencing.",
  related_finding_ids: [],
};

function routeMetrics(id: string) {
  return {
    route_id: id,
    total_distance_miles: 12.5,
    total_time_hours: 2.0,
    drive_time_hours: 1.5,
    service_time_hours: 0.25,
    idle_time_hours: 0.25,
    stop_count: 2,
    stops_per_hour: 1.0,
    stops_per_mile: 0.16,
    sequencing_index: 1.1,
    capacity_utilization: {},
    time_window_violations: 0,
    stops_with_windows: 2,
    shift_overrun_minutes: 0,
    lunch_taken_within_window: true,
  };
}

function route(id: string, lonOffset: number) {
  return {
    route_id: id,
    stops: [1, 2].map((seq) => ({
      route_id: id,
      stop_sequence: seq,
      latitude: 32.78 + seq * 0.01,
      longitude: -96.8 + lonOffset,
      stop_type: "delivery",
      planned_arrival_time: null,
      service_time_minutes: 5,
      time_window_start: "09:00",
      time_window_end: "17:00",
      demand_units: null,
      demand_weight: null,
      demand_volume: null,
      customer_id: `${id}-C${seq}`,
      address: null,
    })),
    depot_lat: 32.7767,
    depot_lon: -96.797,
    planned_start_time: "2026-07-17T08:00:00Z",
    vehicle_capacity_units: null,
    vehicle_capacity_weight: null,
    vehicle_capacity_volume: null,
  };
}

const ANALYSIS = {
  fleet: {
    routes: [route("R-001", 0), route("R-002", 0.02)],
    upload_id: "u1",
    uploaded_at: "2026-07-17T08:00:00Z",
  },
  fleet_metrics: {
    total_routes: 2,
    total_stops: 4,
    total_distance_miles: 25.0,
    total_time_hours: 4.0,
    median_sequencing_index: 1.1,
    routes_over_shift_cap: 0,
    avg_capacity_utilization: {},
  },
  route_metrics: { "R-001": routeMetrics("R-001"), "R-002": routeMetrics("R-002") },
  findings: [FINDING],
  benchmark: null,
  grade: {
    grading_version: "1.0",
    overall: { score: 81.4, letter: "B-" },
    weights: { sequencing: 0.25, fleet: 0.2, time: 0.2, compliance: 0.2, density: 0.15 },
    dimensions: [
      {
        key: "sequencing",
        label: "Sequencing Efficiency",
        score: 88.0,
        letter: "B+",
        basis: "heuristic",
        not_graded: false,
        inputs: { mean_sequencing_index: 1.1 },
        explanation_slot_id: "grade_sequencing",
      },
      {
        key: "fleet",
        label: "Fleet Assignment & Balance",
        score: 100.0,
        letter: "A+",
        basis: "balance_only",
        not_graded: false,
        inputs: { time_cv: 0.0 },
        explanation_slot_id: "grade_fleet",
      },
      {
        key: "time",
        label: "Time Discipline",
        score: 90.0,
        letter: "A-",
        basis: "absolute",
        not_graded: false,
        inputs: { idle_ratio: 0.06 },
        explanation_slot_id: "grade_time",
      },
      {
        key: "compliance",
        label: "Compliance",
        score: 40.5,
        letter: "F",
        basis: "absolute",
        not_graded: false,
        inputs: { violation_rate_pct: 25.0 },
        explanation_slot_id: "grade_compliance",
      },
      {
        key: "density",
        label: "Density & Territory",
        score: null,
        letter: null,
        basis: "insufficient_routes",
        not_graded: true,
        inputs: { reason: "needs at least 2 routes" },
        explanation_slot_id: "grade_density",
      },
    ],
  },
  analyses_run: ["analyze_compliance"],
  analyses_skipped: [["fleet_benchmark", "routes do not share a single depot"]],
  metadata: {},
};

function lineFor(id: string, lonOffset: number) {
  return {
    type: "Feature" as const,
    geometry: {
      type: "LineString" as const,
      coordinates: [
        [-96.797, 32.7767],
        [-96.8 + lonOffset, 32.79],
        [-96.8 + lonOffset, 32.8],
        [-96.797, 32.7767],
      ],
    },
    properties: {
      kind: "actual",
      route_id: id,
      geometry_quality: "approximate",
      stop_count: 2,
      finding_ids: id === "R-001" ? [FINDING.finding_id] : [],
      total_distance_miles: 12.5,
      total_time_hours: 2.0,
      sequencing_index: 1.1,
      distance_gap_pct: null,
    },
  };
}

function stopFor(id: string, seq: number, lonOffset: number) {
  return {
    type: "Feature" as const,
    geometry: {
      type: "Point" as const,
      coordinates: [-96.8 + lonOffset, 32.78 + seq * 0.01],
    },
    properties: {
      kind: "stop",
      route_id: id,
      stop_sequence: seq,
      customer_id: `${id}-C${seq}`,
      address: null,
      stop_type: "delivery",
      service_time_minutes: 5,
      planned_arrival_time: null,
      time_window_start: "09:00",
      time_window_end: "17:00",
      demand_units: null,
      has_violation: id === "R-001",
      finding_ids: [],
    },
  };
}

const GEOJSON = {
  type: "FeatureCollection" as const,
  bbox: [-96.81, 32.77, -96.77, 32.81],
  properties: {
    schema_version: 1,
    geometry_quality: "approximate",
    geometry_note:
      "Some or all route lines are straight segments between stops rather than driven road paths. Distances and times are road-network values from OSRM regardless.",
    has_benchmark: false,
    has_fleet_benchmark: false,
    route_count: 2,
    stop_count: 4,
  },
  features: [
    lineFor("R-001", 0),
    lineFor("R-002", 0.02),
    stopFor("R-001", 1, 0),
    stopFor("R-001", 2, 0),
    stopFor("R-002", 1, 0.02),
    stopFor("R-002", 2, 0.02),
    {
      type: "Feature" as const,
      geometry: { type: "Point" as const, coordinates: [-96.797, 32.7767] },
      properties: { kind: "depot", route_ids: ["R-001", "R-002"] },
    },
  ],
};

/** An empty but valid MapLibre style — no network, no tiles, still a real map. */
const BLANK_STYLE = {
  version: 8,
  name: "e2e-blank",
  sources: {},
  layers: [{ id: "bg", type: "background", paint: { "background-color": "#f0f0f0" } }],
};

async function mockSession(page: Page, overrides: { status?: object } = {}) {
  await page.route("**/tiles.openfreemap.org/**", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(BLANK_STYLE) }),
  );
  await page.route("**/health", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: '{"version":"0.1.0","commit":"e2e"}' }),
  );
  await page.route(`**/sessions/${SESSION}/events`, (route) => route.abort());
  await page.route(`**/sessions/${SESSION}/analysis.json`, (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(ANALYSIS) }),
  );
  await page.route(`**/sessions/${SESSION}/routes.geojson`, (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(GEOJSON) }),
  );
  await page.route(`**/sessions/${SESSION}`, (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ ...STATUS, ...(overrides.status ?? {}) }),
    }),
  );
}

test.describe("results", () => {
  test("the score headlines the page", async ({ page }) => {
    await mockSession(page);
    await page.goto(`/s/${SESSION}`);

    await expect(page.getByRole("heading", { name: /your route quality score/i })).toBeVisible();
    // ASCII in the data, typographic minus on screen.
    await expect(page.getByText("B−", { exact: true })).toBeVisible();
    await expect(page.getByText("81.4")).toBeVisible();
  });

  test("an ungraded dimension says so rather than showing zero", async ({ page }) => {
    await mockSession(page);
    await page.goto(`/s/${SESSION}`);

    const row = page.getByRole("row", { name: /density & territory/i });
    await expect(row).toContainText(/not graded/i);
    // The trap: not_graded means "could not be computed", never "scored 0".
    await expect(row).not.toContainText("0.0");
  });

  test("each dimension shows its number, not just a colour", async ({ page }) => {
    await mockSession(page);
    await page.goto(`/s/${SESSION}`);
    await expect(page.getByText("88.0")).toBeVisible();
    await expect(page.getByText("40.5")).toBeVisible();
  });

  test("approximate geometry is disclosed", async ({ page }) => {
    await mockSession(page);
    await page.goto(`/s/${SESSION}`);
    await expect(page.getByText(/straight segments between stops/i)).toBeVisible();
  });

  test("the disclaimer rides the results too", async ({ page }) => {
    await mockSession(page);
    await page.goto(`/s/${SESSION}`);
    await expect(page.getByText(/not proven mathematical optima/i)).toBeVisible();
  });
});

test.describe("map", () => {
  test("the pipeline's features reach the map", async ({ page }) => {
    await mockSession(page);
    await page.goto(`/s/${SESSION}`);
    await expect(page.getByTestId("route-map")).toBeVisible();

    // Assert on what MapLibre actually built, not on pixels: the layers exist
    // and the source carries every feature the artifact described.
    await expect
      .poll(
        () =>
          page.evaluate(() => document.querySelectorAll("canvas.maplibregl-canvas").length),
        { timeout: 15_000 },
      )
      .toBeGreaterThan(0);
  });

  test("the optimal toggle is disabled when no benchmark ran", async ({ page }) => {
    await mockSession(page);
    await page.goto(`/s/${SESSION}`);
    // has_benchmark is false in the fixture: offering the toggle would promise
    // a view that does not exist.
    await expect(page.getByRole("button", { name: "Solver" })).toBeDisabled();
    await expect(page.getByRole("button", { name: "Your plan" })).toBeEnabled();
  });

  test("routes can be hidden and shown", async ({ page }) => {
    await mockSession(page);
    await page.goto(`/s/${SESSION}`);

    await page.getByText(/routes \(2\/2\)/i).click();
    await page.getByRole("checkbox", { name: "R-001" }).uncheck();
    await expect(page.getByText(/routes \(1\/2\)/i)).toBeVisible();

    await page.getByRole("button", { name: "None", exact: true }).click();
    await expect(page.getByText(/routes \(0\/2\)/i)).toBeVisible();

    await page.getByRole("button", { name: "All", exact: true }).click();
    await expect(page.getByText(/routes \(2\/2\)/i)).toBeVisible();
  });
});

test.describe("findings and the map", () => {
  test("a finding expands to its evidence", async ({ page }) => {
    await mockSession(page);
    await page.goto(`/s/${SESSION}`);

    const finding = page.getByRole("button", { name: /reached after their time window/i });
    await expect(finding).toBeVisible();
    await finding.click();

    await expect(page.getByText(/cannot serve 2 stops/i)).toBeVisible();
    await expect(page.getByText(/suggested next step/i)).toBeVisible();
    await expect(page.getByText("time window violations")).toBeVisible();
  });

  test("selecting a finding is a selection, and clears", async ({ page }) => {
    await mockSession(page);
    await page.goto(`/s/${SESSION}`);

    const finding = page.getByRole("button", { name: /reached after their time window/i });
    await finding.click();
    await expect(finding).toHaveAttribute("aria-expanded", "true");
    await finding.click();
    await expect(finding).toHaveAttribute("aria-expanded", "false");
  });
});

test.describe("failure states", () => {
  test("an interrupted session says the upload survived", async ({ page }) => {
    await mockSession(page, {
      status: {
        ...STATUS,
        state: "failed",
        error: {
          code: "interrupted_by_restart",
          message: "Analysis was interrupted by a server restart before it finished.",
          context: {},
        },
      },
    });
    await page.goto(`/s/${SESSION}`);

    await expect(page.getByRole("heading", { name: /restarted mid-analysis/i })).toBeVisible();
    // The user's file is fine; saying so is the difference between "try again"
    // and an hour hunting for a problem that is not there.
    await expect(page.getByText(/upload was preserved/i)).toBeVisible();
    await expect(page.getByText(SESSION)).toBeVisible();
  });

  test("an expired session explains itself", async ({ page }) => {
    await mockSession(page, { status: { ...STATUS, state: "expired" } });
    await page.goto(`/s/${SESSION}`);
    await expect(page.getByRole("heading", { name: /expired/i })).toBeVisible();
  });

  test("a session in flight shows progress, not a blank page", async ({ page }) => {
    await mockSession(page, {
      status: { ...STATUS, state: "analyzing", progress_pct: 45, stage_detail: "Scoring routes" },
    });
    await page.goto(`/s/${SESSION}`);

    await expect(page.getByRole("heading", { name: /analysing your routes/i })).toBeVisible();
    const bar = page.getByRole("progressbar");
    await expect(bar).toHaveAttribute("aria-valuenow", "45");
    await expect(page.getByText("Scoring routes")).toBeVisible();
  });

  test("an unknown session is a clear 404, not a crash", async ({ page }) => {
    await page.route("**/health", (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: '{"version":"0.1.0","commit":"e2e"}' }),
    );
    await page.route("**/sessions/**", (route) =>
      route.fulfill({ status: 404, contentType: "application/json", body: '{"detail":"Session not found"}' }),
    );
    await page.goto("/s/nope");
    await expect(page.getByRole("heading", { name: /no such session/i })).toBeVisible();
  });
});
