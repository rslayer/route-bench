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
 *  route colors stay the signal.
 *
 *  Retained for the legend swatch and any non-map use. The map itself no
 *  longer paints the solver line a flat grey: a single neutral colour made
 *  every solver tour look like one object, and against the dark basemap
 *  #666666 nearly vanished. It uses the route's own hue, muted. */
export const OPTIMAL_COLOR = "#666666";
export const MIGRATION_COLOR = "#CC6677";
export const DEPOT_COLOR = "#000000";

/**
 * The solver variant of a route colour: same hue, pulled toward mid-grey.
 *
 * Same hue because the two lines describe the same route and must read as a
 * pair. Muted because the plan is the subject and the solver is the reference
 * against it — if both were fully saturated the eye would have no way to tell
 * which one it was being asked to judge.
 *
 * Mixed toward #808080 rather than toward white or black so it behaves the
 * same on the light and dark basemaps. Lightening would have disappeared
 * against positron; darkening would have disappeared against dark-matter.
 */
export function routeColorMuted(routeIds: readonly string[], routeId: string): string {
  return mixToward(routeColor(routeIds, routeId), 0x80, 0.45);
}

function mixToward(hex: string, target: number, amount: number): string {
  const n = Number.parseInt(hex.slice(1), 16);
  const mix = (channel: number): number =>
    Math.round(channel + (target - channel) * amount);
  const r = mix((n >> 16) & 0xff);
  const g = mix((n >> 8) & 0xff);
  const b = mix(n & 0xff);
  return `#${((r << 16) | (g << 8) | b).toString(16).padStart(6, "0")}`;
}

/**
 * Perpendicular screen-pixel offsets that pull the two variants apart.
 *
 * The plan and the solver tour traverse the same stops in a different order,
 * so most of their length lies on identical road segments and one simply
 * covered the other. Opposite signs keep the pair centred on the true path
 * instead of shifting the whole route to one side.
 *
 * Only applied when both are on screen — see RouteMap's mode effect. Shown
 * alone, a route must sit on its road.
 */
export const ACTUAL_OFFSET_PX = -2.5;
export const OPTIMAL_OFFSET_PX = 2.5;

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
