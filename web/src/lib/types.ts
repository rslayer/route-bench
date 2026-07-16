/**
 * Types mirroring the backend contracts.
 *
 * These are hand-maintained against the Python models — there is no codegen, so
 * treat the Python as the source of truth and keep the file references below
 * current when you touch either side:
 *
 *   analysis.json    src/routebench/core/findings.py  (AnalysisReport)
 *   routes.geojson   src/routebench/report/geojson.py (docs/routes-geojson.md)
 *   config JSON      src/routebench/core/config.py    (AnalysisConfig)
 *   status.json      src/routebench/app/sessions.py   (SessionStatus)
 */

// ---------------------------------------------------------------------------
// Sessions — src/routebench/app/sessions.py
// ---------------------------------------------------------------------------

/** A session is finished only in succeeded/failed/expired; the rest are in flight. */
export type SessionState =
  | "queued"
  | "validating"
  | "analyzing"
  | "writing"
  | "rendering"
  | "succeeded"
  | "failed"
  | "expired";

export const TERMINAL_STATES: ReadonlySet<SessionState> = new Set([
  "succeeded",
  "failed",
  "expired",
]);

export interface SessionError {
  /** Machine-readable: "interrupted_by_restart", "stale", "JOB_TIMEOUT", … */
  code: string;
  message: string;
  context?: Record<string, unknown>;
}

export interface SessionArtifacts {
  report_html: string;
  report_pdf: string;
  analysis_json: string;
  telemetry_json: string;
  routes_geojson: string;
}

export interface CostSummary {
  input_tokens: number;
  output_tokens: number;
  llm_cost_usd: number;
  total_cost_usd: number;
}

export interface SessionStatus {
  session_id: string;
  state: SessionState;
  progress_pct: number;
  stage_detail: string;
  created_at: string;
  updated_at: string;
  error: SessionError | null;
  artifacts: SessionArtifacts | null;
  cost: CostSummary | null;
}

// ---------------------------------------------------------------------------
// Findings — src/routebench/core/findings.py
// ---------------------------------------------------------------------------

export type FindingCategory =
  | "sequencing"
  | "time_pressure"
  | "utilization"
  | "compliance"
  | "territory"
  | "dispatch"
  | "outlier";

export type FindingSeverity = "info" | "low" | "medium" | "high" | "critical";

export const SEVERITY_ORDER: Record<FindingSeverity, number> = {
  critical: 0,
  high: 1,
  medium: 2,
  low: 3,
  info: 4,
};

export interface FindingEvidence {
  metric_name: string;
  actual_value: number;
  comparison_value: number | null;
  comparison_type: "fleet_median" | "threshold" | "optimal" | "peer" | null;
  unit: string;
}

export interface FindingReference {
  route_ids: string[];
  /** [route_id, stop_sequence] pairs. */
  stop_sequences: [string, number][];
}

export interface Finding {
  finding_id: string;
  category: FindingCategory;
  severity: FindingSeverity;
  confidence: number;
  title: string;
  evidence: FindingEvidence[];
  references: FindingReference;
  hypothesis: string;
  suggested_investigation: string;
  related_finding_ids: string[];
}

export interface RouteMetrics {
  route_id: string;
  total_distance_miles: number;
  total_time_hours: number;
  drive_time_hours: number;
  service_time_hours: number;
  idle_time_hours: number;
  stop_count: number;
  stops_per_hour: number;
  stops_per_mile: number;
  sequencing_index: number | null;
  capacity_utilization: Record<string, number>;
  time_window_violations: number;
  /** Denominator for the violation rate — stops that HAVE a window. */
  stops_with_windows: number;
  shift_overrun_minutes: number;
  lunch_taken_within_window: boolean;
}

export interface FleetMetrics {
  total_routes: number;
  total_stops: number;
  total_distance_miles: number;
  total_time_hours: number;
  median_sequencing_index: number | null;
  routes_over_shift_cap: number;
  avg_capacity_utilization: Record<string, number>;
}

export interface StopMigration {
  route_id: string;
  stop_sequence: number;
  customer_id: string | null;
  from_route: string;
  to_route: string;
}

/**
 * Gap fields are percentages (0-100) and MAY BE NEGATIVE — a negative gap means
 * the solver found nothing better than the plan. Render that as "within solver
 * reach", never as a saving, and never clamp it. See Phase 10.5 Part B.
 */
export interface RouteBenchmark {
  route_id: string;
  actual_distance_miles: number;
  optimal_distance_miles: number;
  distance_gap_pct: number;
  actual_time_hours: number;
  optimal_time_hours: number;
  time_gap_pct: number;
  improvement_gap_pct: number;
  /** Solver tour as matrix indices (1..n; 0 is the depot). Empty if unorderable. */
  stop_order: number[];
}

export interface FleetBenchmark {
  actual_total_distance: number;
  optimal_total_distance: number;
  stop_migrations: StopMigration[];
  improvement_gap_pct: number;
}

export interface BenchmarkResult {
  per_route: Record<string, RouteBenchmark>;
  /** Null when the fleet VRPTW was skipped (1 route, multi-depot, >300 stops). */
  fleet_level: FleetBenchmark | null;
}

export interface Stop {
  route_id: string;
  stop_sequence: number;
  latitude: number;
  longitude: number;
  stop_type: "depot" | "delivery" | "pickup";
  planned_arrival_time: string | null;
  service_time_minutes: number;
  time_window_start: string | null;
  time_window_end: string | null;
  demand_units: number | null;
  demand_weight: number | null;
  demand_volume: number | null;
  customer_id: string | null;
  address: string | null;
}

export interface Route {
  route_id: string;
  stops: Stop[];
  depot_lat: number;
  depot_lon: number;
  planned_start_time: string;
  vehicle_capacity_units: number | null;
  vehicle_capacity_weight: number | null;
  vehicle_capacity_volume: number | null;
}

export interface Fleet {
  routes: Route[];
  upload_id: string;
  uploaded_at: string;
}

// ---------------------------------------------------------------------------
// Grade — src/routebench/analysis/scoring/grading.py
// ---------------------------------------------------------------------------

/** ASCII, not typographic: "A-" not "A−". Render the pretty minus if you like. */
export type GradeLetter =
  | "A+" | "A" | "A-"
  | "B+" | "B" | "B-"
  | "C+" | "C" | "C-"
  | "D+" | "D" | "D-"
  | "F";

export type DimensionKey =
  | "sequencing"
  | "fleet"
  | "time"
  | "compliance"
  | "density";

/**
 * What a dimension's score was anchored to. It matters to the reader:
 * "heuristic" is a weaker proxy than "benchmark", and *_only bases mean part of
 * the dimension could not be assessed for this fleet.
 */
export type GradeBasis =
  | "benchmark"
  | "heuristic"
  | "balance_only"
  | "absolute"
  | "operational_only"
  | "fleet_relative"
  | "insufficient_routes"
  | "insufficient_data";

export interface OverallGrade {
  /** Null when nothing could be graded. */
  score: number | null;
  letter: GradeLetter | null;
}

export interface DimensionGrade {
  key: DimensionKey;
  label: string;
  /** Null when not_graded. Do NOT render this as 0. */
  score: number | null;
  letter: GradeLetter | null;
  basis: GradeBasis;
  /** True means "could not be computed for this fleet", never "scored badly". */
  not_graded: boolean;
  /** Every value is recomputable from metrics elsewhere in the artifact. */
  inputs: Record<string, number | string>;
  explanation_slot_id: string;
}

export interface Grade {
  /** Reports display the rubric version they were graded under. */
  grading_version: string;
  overall: OverallGrade;
  weights: Record<DimensionKey, number>;
  dimensions: DimensionGrade[];
}

export interface AnalysisReport {
  fleet: Fleet;
  fleet_metrics: FleetMetrics;
  route_metrics: Record<string, RouteMetrics>;
  findings: Finding[];
  benchmark: BenchmarkResult | null;
  /** Null for artifacts written before the grading engine (Phase 10.6). */
  grade: Grade | null;
  analyses_run: string[];
  /** [tool_name, reason] — why a tool did not run, e.g. the fleet-benchmark cap. */
  analyses_skipped: [string, string][];
  metadata: Record<string, unknown>;
}

// ---------------------------------------------------------------------------
// routes.geojson — docs/routes-geojson.md
// ---------------------------------------------------------------------------

export type FeatureKind = "actual" | "optimal" | "stop" | "depot" | "migration";

/** "approximate" means straight segments, not driven roads. Surface it. */
export type GeometryQuality = "exact" | "approximate";

interface BaseProps {
  kind: FeatureKind;
  finding_ids?: string[];
}

export interface ActualRouteProps extends BaseProps {
  kind: "actual";
  route_id: string;
  geometry_quality: GeometryQuality;
  stop_count: number;
  finding_ids: string[];
  total_distance_miles: number | null;
  total_time_hours: number | null;
  sequencing_index: number | null;
  distance_gap_pct: number | null;
}

export interface OptimalRouteProps extends BaseProps {
  kind: "optimal";
  route_id: string;
  geometry_quality: GeometryQuality;
  stop_order: number[];
  finding_ids: string[];
  total_distance_miles: number;
  total_time_hours: number;
  distance_gap_pct: number;
  improvement_gap_pct: number;
}

export interface StopProps extends BaseProps {
  kind: "stop";
  route_id: string;
  stop_sequence: number;
  customer_id: string | null;
  address: string | null;
  stop_type: string;
  service_time_minutes: number;
  planned_arrival_time: string | null;
  time_window_start: string | null;
  time_window_end: string | null;
  demand_units: number | null;
  /** Route-level: the route carries a compliance finding, not necessarily this stop. */
  has_violation: boolean;
  finding_ids: string[];
}

export interface DepotProps extends BaseProps {
  kind: "depot";
  /** Deduplicated by coordinate, so one marker may serve several routes. */
  route_ids: string[];
}

export interface MigrationProps extends BaseProps {
  kind: "migration";
  route_id: string;
  stop_sequence: number;
  customer_id: string | null;
  from_route: string;
  to_route: string;
  finding_ids: string[];
}

export type RouteFeatureProps =
  | ActualRouteProps
  | OptimalRouteProps
  | StopProps
  | DepotProps
  | MigrationProps;

export interface RoutesGeoJSONProps {
  schema_version: number;
  geometry_quality: GeometryQuality;
  geometry_note: string;
  has_benchmark: boolean;
  has_fleet_benchmark: boolean;
  route_count: number;
  stop_count: number;
}

export interface RouteFeature<P = RouteFeatureProps> {
  type: "Feature";
  geometry:
    | { type: "Point"; coordinates: [number, number] }
    | { type: "LineString"; coordinates: [number, number][] };
  properties: P;
}

export interface RoutesGeoJSON {
  type: "FeatureCollection";
  features: RouteFeature[];
  properties: RoutesGeoJSONProps;
  /** [west, south, east, north]. Absent for an empty fleet. */
  bbox?: [number, number, number, number];
}

// ---------------------------------------------------------------------------
// Config — src/routebench/core/config.py (AnalysisConfig)
// ---------------------------------------------------------------------------

export interface WorkRules {
  max_shift_hours: number;
  pre_trip_minutes: number;
  post_trip_minutes: number;
  lunch_minutes: number;
  lunch_after_hours: number;
  enforce_time_windows: boolean;
}

export interface ServiceTimeModel {
  default_minutes: number;
}

export interface TrafficBand {
  /** "HH:MM", inclusive. */
  start: string;
  /** "HH:MM", exclusive. */
  end: string;
  /** Multiplies free-flow speed; below 1.0 slows travel. */
  speed_factor: number;
}

export interface TrafficProfile {
  bands: TrafficBand[];
  default_factor: number;
}

/** Named profiles the API accepts in place of an inline TrafficProfile. */
export type NamedTrafficProfile = "urban_us";

export interface AnalysisConfig {
  work_rules: WorkRules;
  service_time: ServiceTimeModel;
  traffic: TrafficProfile | NamedTrafficProfile;
  sequencing_threshold: number;
  underutilization_threshold: number;
  overutilization_threshold: number;
  include_benchmark: boolean;
  include_pdf: boolean;
}

// ---------------------------------------------------------------------------
// Build identity — GET /health
// ---------------------------------------------------------------------------

export interface BuildInfo {
  version: string;
  /** Short sha, or "unknown" when the build baked none in. */
  commit: string;
}
