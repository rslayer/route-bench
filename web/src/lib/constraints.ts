/**
 * What RouteBench models, and what it does not.
 *
 * Content-managed here so the honest list stays current in one place: the
 * upload flow and /how-it-works both read it, and a claim made in two places
 * drifts.
 *
 * SUPPORTED entries map 1:1 to AnalysisConfig fields. The panel state IS the
 * config JSON sent to POST /sessions — no hidden defaults that differ from what
 * the user saw.
 */

export type ControlKind = "toggle" | "number" | "select";

export interface SupportedConstraint {
  id: string;
  label: string;
  /** What it does, in the operator's language, not the model's. */
  description: string;
  kind: ControlKind;
  /** Dotted path into AnalysisConfig, for the serializer. */
  configPath: string;
  unit?: string;
  min?: number;
  max?: number;
  step?: number;
  options?: { value: string; label: string }[];
  /** Only meaningful when the upload carries the columns it needs. */
  requiresColumns?: string[];
}

export const SUPPORTED_CONSTRAINTS: readonly SupportedConstraint[] = [
  {
    id: "time_windows",
    label: "Delivery time windows",
    description:
      "Grade whether stops are reached inside their committed window, and make the solver respect windows when it re-sequences.",
    kind: "toggle",
    configPath: "work_rules.enforce_time_windows",
    requiresColumns: ["time_window_start", "time_window_end"],
  },
  {
    id: "max_shift_hours",
    label: "Maximum shift length",
    description: "The longest a driver's day may run, door to door.",
    kind: "number",
    configPath: "work_rules.max_shift_hours",
    unit: "hours",
    min: 1,
    max: 24,
    step: 0.5,
  },
  {
    id: "pre_post_trip",
    label: "Pre-trip / post-trip time",
    description: "Fixed time at the depot before departure and after return.",
    kind: "number",
    configPath: "work_rules.pre_trip_minutes",
    unit: "minutes",
    min: 0,
    max: 120,
    step: 5,
  },
  {
    id: "lunch",
    label: "Lunch break",
    description: "A single unpaid break, inserted once the shift passes a threshold.",
    kind: "number",
    configPath: "work_rules.lunch_minutes",
    unit: "minutes",
    min: 0,
    max: 120,
    step: 5,
  },
  {
    id: "capacity",
    label: "Vehicle capacity",
    description:
      "Units, weight, or volume limits per vehicle. Applied only where your file carries the columns.",
    kind: "toggle",
    configPath: "_capacity_enabled",
    requiresColumns: ["vehicle_capacity_units", "demand_units"],
  },
  {
    id: "service_time",
    label: "Per-stop service time",
    description:
      "How long a driver spends at each stop. Your file can override this per stop with a service_time_minutes column.",
    kind: "number",
    configPath: "service_time.default_minutes",
    unit: "minutes",
    min: 0,
    max: 240,
    step: 1,
  },
  {
    id: "traffic",
    label: "Traffic profile",
    description:
      "Scale travel times by time of day. Not live traffic — a recurring-congestion approximation.",
    kind: "select",
    configPath: "traffic",
    options: [
      { value: "free_flow", label: "Free-flow (no adjustment)" },
      { value: "urban_us", label: "Urban US (0.75× at 07:00–09:00, 0.80× at 16:00–18:30)" },
    ],
  },
  {
    id: "fleet_benchmark",
    label: "Fleet benchmark",
    description:
      "Re-optimise stop-to-route assignment across the whole fleet, not just the order within each route.",
    kind: "toggle",
    configPath: "include_benchmark",
  },
] as const;

export type RoadmapTag = "Planned" | "Under consideration";

export interface UnsupportedConstraint {
  label: string;
  /** Why it matters, so the gap is legible rather than a bare "no". */
  note: string;
  tag: RoadmapTag;
}

/**
 * Stated plainly rather than buried. A benchmarking tool that hides its own
 * limits is not a referee, and a user whose operation depends on one of these
 * should know before they read a grade, not after.
 */
export const UNSUPPORTED_CONSTRAINTS: readonly UnsupportedConstraint[] = [
  {
    label: "Multiple depots per fleet",
    note: "Every route must start and end at the same depot. Multi-depot fleets are scored per route, but the cross-route benchmark is skipped.",
    tag: "Planned",
  },
  {
    label: "Heterogeneous vehicle types",
    note: "All vehicles are treated as interchangeable. A mixed fleet of vans and box trucks is graded as though any vehicle could take any route.",
    tag: "Planned",
  },
  {
    label: "Driver skills and stop compatibility",
    note: "Certifications, equipment, and customer-specific driver requirements are not modelled, so the solver may suggest an assignment a real driver cannot make.",
    tag: "Under consideration",
  },
  {
    label: "Pickup-and-delivery pairs",
    note: "Stops are independent. Paired pickup-then-dropoff work is not modelled.",
    tag: "Under consideration",
  },
  {
    label: "Driver breaks beyond a single lunch",
    note: "One break is modelled. Statutory rest rules and multi-break schedules are not.",
    tag: "Under consideration",
  },
  {
    label: "Live or historical traffic data",
    note: "Travel times come from OpenStreetMap road data, optionally scaled by a time-of-day profile. RouteBench does not consult live or historical traffic.",
    tag: "Planned",
  },
  {
    label: "Planned versus actuals",
    note: "RouteBench grades the plan you uploaded. It has no telematics feed and cannot tell you what the drivers actually did.",
    tag: "Planned",
  },
] as const;

/**
 * Verbatim on /how-it-works and in the results footer. Do not paraphrase per
 * page — the point of a disclaimer is that it says the same thing everywhere.
 */
export const SOLVER_DISCLAIMER =
  "Benchmark solutions are the best found within a fixed compute budget using metaheuristic optimization; they are not proven mathematical optima. Reported savings are therefore conservative. Travel times are estimates and do not reflect live traffic. RouteBench findings are analytical aids, not operational routing instructions.";

export interface SolverEngine {
  name: string;
  scope: string;
  description: string;
}

/** A list so a future ensemble engine appends without a redesign. */
export const SOLVER_ENGINES: readonly SolverEngine[] = [
  {
    name: "Sequencing benchmark (TSPTW)",
    scope: "One route at a time",
    description:
      "Takes the stops you assigned to a route and looks for a better order for them, respecting time windows and the shift cap. The gap between your order and the solver's is the sequencing opportunity.",
  },
  {
    name: "Fleet re-optimisation (VRPTW)",
    scope: "The whole fleet at once",
    description:
      "Re-assigns stops across routes as well as re-ordering within them. This is what surfaces a stop that would be cheaper to serve from a neighbouring route.",
  },
] as const;
