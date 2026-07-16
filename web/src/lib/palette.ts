/**
 * Colorblind-safe palette (Task 7).
 *
 * Route colors come from Paul Tol's "muted" qualitative scheme, which is
 * designed to stay distinguishable under deuteranopia, protanopia, and
 * tritanopia — the common cases. Do not reorder casually: adjacent entries are
 * chosen to contrast with each other, so a fleet of 3 routes uses the first
 * three and must still be readable.
 *
 * Color alone never carries meaning. Severity pairs its color with a label,
 * and the map pairs route color with a numbered marker, so a viewer who cannot
 * separate two hues can still read the route.
 */

import type { FindingSeverity } from "./types";

/** Paul Tol muted — 9 hues that survive the common colorblindness types. */
export const ROUTE_COLORS = [
  "#332288", // indigo
  "#88CCEE", // cyan
  "#44AA99", // teal
  "#117733", // green
  "#999933", // olive
  "#DDCC77", // sand
  "#CC6677", // rose
  "#882255", // wine
  "#AA4499", // purple
] as const;

/**
 * Stable color for a route.
 *
 * Keyed on the route's index in a sorted id list, never on iteration order, so
 * the same route keeps its color across re-renders and between the map and the
 * findings list. A fleet larger than the palette wraps — 50 routes cannot all
 * be distinct anyway, which is what the visibility checkboxes are for.
 */
export function routeColor(routeIds: readonly string[], routeId: string): string {
  const index = [...routeIds].sort().indexOf(routeId);
  if (index < 0) return ROUTE_COLORS[0];
  return ROUTE_COLORS[index % ROUTE_COLORS.length] ?? ROUTE_COLORS[0];
}

/** The solver's tour, drawn against the plan. Deliberately neutral so the
 *  route colors stay the signal. */
export const OPTIMAL_COLOR = "#666666";
export const MIGRATION_COLOR = "#CC6677";
export const DEPOT_COLOR = "#000000";

export const SEVERITY_COLORS: Record<FindingSeverity, string> = {
  critical: "#882255",
  high: "#CC6677",
  medium: "#DDCC77",
  low: "#88CCEE",
  info: "#44AA99",
};

/** Text on a severity chip. The sand/cyan/teal fills are too light for white. */
export const SEVERITY_TEXT: Record<FindingSeverity, string> = {
  critical: "#FFFFFF",
  high: "#FFFFFF",
  medium: "#000000",
  low: "#000000",
  info: "#000000",
};
