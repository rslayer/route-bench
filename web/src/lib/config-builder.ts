/**
 * Panel state -> AnalysisConfig.
 *
 * The promise the constraints panel makes is that what the user saw is exactly
 * what runs. So this is the ONLY place a config is built, it takes every value
 * from panel state, and it injects no defaults of its own. If a field is not in
 * PanelState, the user could not see it and cannot be surprised by it.
 *
 * Mirrors src/routebench/core/config.py (AnalysisConfig). The API validates
 * regardless; a mismatch here surfaces as a 422 rather than a wrong analysis.
 */

import type { AnalysisConfig, TrafficProfile } from "./types";

export type TrafficChoice = "free_flow" | "urban_us";

/**
 * Everything the panel can change. Deliberately flat: a checkbox plus its value
 * is two pieces of state, and nesting them to mirror the config shape would put
 * the serializer's structure into the UI's head.
 */
export interface PanelState {
  enforceTimeWindows: boolean;

  maxShiftHours: number;

  /** Off means zero minutes — the constraint stops applying. */
  prePostTripEnabled: boolean;
  preTripMinutes: number;
  postTripMinutes: number;

  lunchEnabled: boolean;
  lunchMinutes: number;
  lunchAfterHours: number;

  enforceCapacity: boolean;

  serviceDefaultMinutes: number;

  traffic: TrafficChoice;

  includeBenchmark: boolean;
  includePdf: boolean;
}

/** Matches the backend's own defaults, so an untouched panel is a plain run. */
export const DEFAULT_PANEL: PanelState = {
  enforceTimeWindows: true,
  maxShiftHours: 12,
  prePostTripEnabled: true,
  preTripMinutes: 15,
  postTripMinutes: 15,
  lunchEnabled: true,
  lunchMinutes: 30,
  lunchAfterHours: 6,
  enforceCapacity: true,
  serviceDefaultMinutes: 5,
  traffic: "free_flow",
  includeBenchmark: true,
  includePdf: false,
};

/** Free-flow is the identity profile: no bands, factor 1.0, nothing changes. */
const FREE_FLOW: TrafficProfile = { bands: [], default_factor: 1.0 };

/**
 * The urban_us bands, spelled out rather than sent as the name "urban_us".
 *
 * The API accepts either, but sending the bands means the config the user
 * agreed to is legible in the artifact and in any support conversation —
 * "urban_us" is only meaningful if you know what it expands to, and the whole
 * point of the panel is that nothing is hidden.
 */
const URBAN_US: TrafficProfile = {
  bands: [
    { start: "07:00", end: "09:00", speed_factor: 0.75 },
    { start: "16:00", end: "18:30", speed_factor: 0.8 },
  ],
  default_factor: 1.0,
};

export function trafficProfileFor(choice: TrafficChoice): TrafficProfile {
  return choice === "urban_us" ? URBAN_US : FREE_FLOW;
}

export function buildConfig(panel: PanelState): AnalysisConfig {
  return {
    work_rules: {
      max_shift_hours: panel.maxShiftHours,
      // Turning these off means zero minutes, which is how the backend already
      // expresses "no pre/post-trip" — there is no separate flag, and inventing
      // one would put the UI's vocabulary into the config.
      pre_trip_minutes: panel.prePostTripEnabled ? panel.preTripMinutes : 0,
      post_trip_minutes: panel.prePostTripEnabled ? panel.postTripMinutes : 0,
      lunch_minutes: panel.lunchEnabled ? panel.lunchMinutes : 0,
      lunch_after_hours: panel.lunchAfterHours,
      enforce_time_windows: panel.enforceTimeWindows,
      enforce_capacity: panel.enforceCapacity,
    },
    service_time: {
      default_minutes: panel.serviceDefaultMinutes,
    },
    traffic: trafficProfileFor(panel.traffic),
    // Not exposed in the panel: these are analysis thresholds, not operational
    // constraints, and a user has no basis to tune them. They are sent
    // explicitly at the backend's own defaults so the config is complete and
    // self-describing rather than relying on server-side fill-in.
    sequencing_threshold: 1.3,
    underutilization_threshold: 0.6,
    overutilization_threshold: 0.95,
    include_benchmark: panel.includeBenchmark,
    include_pdf: panel.includePdf,
  };
}

/**
 * A short, human-readable account of what was turned off.
 *
 * The report's methodology page states which constraints were active; this is
 * the same statement at the moment of choosing, so a user is never surprised
 * later by a setting they do not remember making.
 */
export function describeDeviations(panel: PanelState): string[] {
  const notes: string[] = [];
  if (!panel.enforceTimeWindows) {
    notes.push("Time windows will not be enforced — the benchmark may serve stops outside them.");
  }
  if (!panel.enforceCapacity) {
    notes.push("Vehicle capacity will be ignored, where your file provides it.");
  }
  if (!panel.lunchEnabled) {
    notes.push("No lunch break will be modelled.");
  }
  if (!panel.prePostTripEnabled) {
    notes.push("No pre-trip or post-trip time will be modelled.");
  }
  if (!panel.includeBenchmark) {
    notes.push(
      "The benchmark will not run, so Sequencing Efficiency falls back to a weaker heuristic and cross-route savings are not measured.",
    );
  }
  if (panel.traffic !== "free_flow") {
    notes.push("Travel times will be scaled by the Urban US traffic profile.");
  }
  return notes;
}
