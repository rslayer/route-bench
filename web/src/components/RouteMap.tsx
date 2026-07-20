"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import maplibregl, { type ExpressionSpecification, type Map as MapLibreMap } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import {
  ACTUAL_OFFSET_PX,
  DEPOT_COLOR,
  MIGRATION_COLOR,
  OPTIMAL_OFFSET_PX,
  routeColor,
  routeColorMuted,
} from "@/lib/palette";
import type { RouteFeature, RoutesGeoJSON, StopProps } from "@/lib/types";

/**
 * The map.
 *
 * Everything is drawn from the one GeoJSON source the pipeline emitted, using
 * data-driven layer styling. Nothing is a DOM marker: the target is 50 routes
 * and 5,000 stops at 60fps, and 5,000 absolutely-positioned divs would die long
 * before that. Colour, visibility, and highlight are all expressions over
 * feature properties, so a re-render is a paint, not a rebuild.
 *
 * The UI renders geography and never computes it — every coordinate here came
 * from routes.geojson.
 */

// OpenFreeMap: no key, no quota, no tracking. Protomaps/R2 is the fallback if
// it ever goes away; the style URL is the only thing that changes.
//
// The basemap follows the page theme. A bright map inside a dark page is the
// single most jarring thing about a half-themed app, and it also wrecks
// contrast: our route colours are chosen against the page, not against a
// basemap that disagrees with it.
const STYLE_URL: Record<"light" | "dark", string> = {
  light: "https://tiles.openfreemap.org/styles/positron",
  dark: "https://tiles.openfreemap.org/styles/dark",
};

/** Read the theme the inline script stamped on <html>. */
function currentTheme(): "light" | "dark" {
  if (typeof document === "undefined") return "light";
  return document.documentElement.dataset.theme === "dark" ? "dark" : "light";
}

export type MapMode = "actual" | "optimal" | "split";

interface Props {
  geojson: RoutesGeoJSON;
  routeIds: string[];
  visibleRoutes: Set<string>;
  mode: MapMode;
  /** Reassignment arrows, off unless asked for — see SessionView. */
  showMigrations: boolean;
  /** Finding the user selected; its routes/stops get highlighted and zoomed. */
  selectedFindingId: string | null;
  /** Route the user selected, which filters the findings list. */
  selectedRouteId: string | null;
  onSelectRoute: (routeId: string | null) => void;
}

/**
 * `match` over route_id so colour is a paint property, not a per-feature loop.
 *
 * Built as a plain array then narrowed: MapLibre's ExpressionSpecification
 * requires at least one case pair, which the compiler cannot prove from a
 * spread, and the empty case is handled above.
 */
function routeColorExpression(
  routeIds: string[],
  variant: "plan" | "solver" = "plan",
): ExpressionSpecification | string {
  const pick = variant === "solver" ? routeColorMuted : routeColor;
  if (routeIds.length === 0) return "#888888";
  const expr: unknown[] = ["match", ["get", "route_id"]];
  for (const id of routeIds) {
    expr.push(id, pick(routeIds, id));
  }
  expr.push("#888888"); // a feature with no route_id (a depot) falls through
  return expr as unknown as ExpressionSpecification;
}

/** Visible ∧ right-kind. Filters keep hidden routes out of the render entirely. */
function kindFilter(kind: string, visible: string[]): ExpressionSpecification {
  return [
    "all",
    ["==", ["get", "kind"], kind],
    ["in", ["get", "route_id"], ["literal", visible]],
  ] as ExpressionSpecification;
}

function bboxOf(features: RouteFeature[]): [number, number, number, number] | null {
  let west = Infinity;
  let south = Infinity;
  let east = -Infinity;
  let north = -Infinity;
  for (const f of features) {
    const coords =
      f.geometry.type === "Point" ? [f.geometry.coordinates] : f.geometry.coordinates;
    for (const [lon, lat] of coords) {
      west = Math.min(west, lon);
      east = Math.max(east, lon);
      south = Math.min(south, lat);
      north = Math.max(north, lat);
    }
  }
  return Number.isFinite(west) ? [west, south, east, north] : null;
}

function stopTooltip(props: StopProps): string {
  const rows: [string, string][] = [];
  if (props.customer_id) rows.push(["Customer", props.customer_id]);
  if (props.planned_arrival_time) {
    const t = props.planned_arrival_time.slice(11, 16);
    if (t) rows.push(["Planned arrival", t]);
  }
  if (props.time_window_start || props.time_window_end) {
    rows.push(["Window", `${props.time_window_start ?? "—"} – ${props.time_window_end ?? "—"}`]);
  }
  rows.push(["Service", `${props.service_time_minutes} min`]);
  if (props.demand_units != null) rows.push(["Demand", String(props.demand_units)]);

  const badge = props.has_violation
    ? '<span class="map-badge">Compliance finding on this route</span>'
    : "";

  return `
    <div class="map-pop">
      <div class="map-pop-head">${props.route_id} · stop ${props.stop_sequence}</div>
      ${badge}
      <dl>${rows.map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`).join("")}</dl>
    </div>`;
}

export default function RouteMap({
  geojson,
  routeIds,
  visibleRoutes,
  mode,
  showMigrations,
  selectedFindingId,
  selectedRouteId,
  onSelectRoute,
}: Props) {
  const container = useRef<HTMLDivElement>(null);
  const map = useRef<MapLibreMap | null>(null);
  const popup = useRef<maplibregl.Popup | null>(null);
  // Which basemap is currently loaded, so a theme change is detected once.
  const appliedTheme = useRef<"light" | "dark">("light");
  const [ready, setReady] = useState(false);

  const visible = useMemo(() => [...visibleRoutes], [visibleRoutes]);
  const colorExpr = useMemo(() => routeColorExpression(routeIds), [routeIds]);
  const mutedColorExpr = useMemo(() => routeColorExpression(routeIds, "solver"), [routeIds]);

  // --- create once ---------------------------------------------------------
  useEffect(() => {
    if (!container.current || map.current) return;

    appliedTheme.current = currentTheme();
    const instance = new maplibregl.Map({
      container: container.current,
      style: STYLE_URL[appliedTheme.current],
      bounds: geojson.bbox ?? [-97, 32, -96, 33],
      fitBoundsOptions: { padding: 48 },
      attributionControl: { compact: true },
    });
    instance.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
    instance.addControl(new maplibregl.ScaleControl({ unit: "imperial" }), "bottom-left");
    // Keyboard users need to reach the map's own controls; MapLibre handles
    // pan/zoom keys itself once the canvas has focus.
    instance.getCanvas().setAttribute("tabindex", "0");
    instance.getCanvas().setAttribute("aria-label", "Route map");

    const buildLayers = () => {
      // generateId assigns each feature a numeric id from its index in the
      // features array. setFeatureState needs an id and the pipeline's GeoJSON
      // has none, so without this every dim/highlight call silently no-ops.
      instance.addSource("routes", {
        type: "geojson",
        data: geojson as never,
        generateId: true,
      });

      // Order matters: optimal under actual, stops over both, depots on top.
      //
      // The two variants are the same route re-sequenced, so most of their
      // length runs along identical road segments. Drawn concentrically they
      // were indistinguishable — the solver line sat exactly under the plan
      // line and was simply covered. Colour alone cannot fix that; the lines
      // have to not occupy the same pixels.
      //
      // So they are separated on four channels at once, because any one of
      // them fails somewhere:
      //   offset  — pulls coincident segments apart into visible tramlines.
      //             The load-bearing one. Sign is opposite per variant so the
      //             gap is symmetric about the true path rather than shifting
      //             the whole route sideways.
      //   dash    — survives greyscale, printing, and colour-blind viewers.
      //   width   — the plan is heavier; it is the subject, the solver is the
      //             reference.
      //   colour  — both carry the route's hue so it is still obvious the two
      //             belong together, with the solver desaturated toward the
      //             basemap so it reads as annotation rather than a peer.
      //
      // Offsets are in screen pixels and do not scale with zoom, which is the
      // behaviour we want: the point is legibility of overlap, not a real
      // distance, and at high zoom the lines genuinely diverge anyway.
      instance.addLayer({
        id: "optimal-lines",
        type: "line",
        source: "routes",
        filter: kindFilter("optimal", visible),
        layout: { "line-cap": "round", "line-join": "round" },
        paint: {
          "line-color": mutedColorExpr,
          "line-width": ["case", ["boolean", ["feature-state", "highlighted"], false], 3.5, 2],
          "line-dasharray": [1.5, 1.5],
          // Starts at 0, not OPTIMAL_OFFSET_PX. The default mode is "actual",
          // where the offset must be 0; the mode effect sets ±offset only in
          // split. Baking a non-zero offset here would draw the route off its
          // road for the frame before that effect runs.
          "line-offset": 0,
          // Dimmed in step with the plan line, so selecting a finding fades
          // both variants of the unrelated routes rather than leaving a thicket
          // of solver lines behind. Base opacity is below the plan line's so it
          // reads as the reference rather than competing with it.
          "line-opacity": ["case", ["boolean", ["feature-state", "dimmed"], false], 0.1, 0.75],
        },
      });

      instance.addLayer({
        id: "actual-lines",
        type: "line",
        source: "routes",
        filter: kindFilter("actual", visible),
        layout: { "line-cap": "round", "line-join": "round" },
        paint: {
          "line-color": colorExpr,
          "line-width": ["case", ["boolean", ["feature-state", "highlighted"], false], 5, 3],
          // 0 at creation; the mode effect applies ACTUAL_OFFSET_PX only in
          // split. See the optimal layer above.
          "line-offset": 0,
          "line-opacity": ["case", ["boolean", ["feature-state", "dimmed"], false], 0.15, 0.85],
        },
      });

      instance.addLayer({
        id: "migration-lines",
        type: "line",
        source: "routes",
        filter: ["==", ["get", "kind"], "migration"],
        paint: {
          "line-color": MIGRATION_COLOR,
          "line-width": 1.5,
          "line-dasharray": [1, 2],
          "line-opacity": 0.8,
        },
      });

      instance.addLayer({
        id: "stops",
        type: "circle",
        source: "routes",
        filter: kindFilter("stop", visible),
        paint: {
          "circle-color": colorExpr,
          // Zoom-interpolated so a dense fleet stays legible when zoomed out
          // instead of becoming a solid blob.
          "circle-radius": ["interpolate", ["linear"], ["zoom"], 8, 3, 12, 7, 16, 11],
          "circle-stroke-width": [
            "case",
            ["boolean", ["get", "has_violation"], false],
            2,
            1,
          ],
          "circle-stroke-color": [
            "case",
            ["boolean", ["get", "has_violation"], false],
            "#882255",
            "#ffffff",
          ],
          "circle-opacity": ["case", ["boolean", ["feature-state", "dimmed"], false], 0.2, 1],
        },
      });

      instance.addLayer({
        id: "stop-labels",
        type: "symbol",
        source: "routes",
        filter: kindFilter("stop", visible),
        // Numbers only appear once they can be read; below that they are noise.
        minzoom: 12,
        layout: {
          "text-field": ["to-string", ["get", "stop_sequence"]],
          "text-size": 10,
          "text-allow-overlap": false,
          "text-ignore-placement": false,
        },
        paint: {
          "text-color": "#ffffff",
          "text-halo-color": "#00000066",
          "text-halo-width": 1,
        },
      });

      instance.addLayer({
        id: "depots",
        type: "circle",
        source: "routes",
        filter: ["==", ["get", "kind"], "depot"],
        paint: {
          "circle-color": DEPOT_COLOR,
          "circle-radius": 7,
          "circle-stroke-width": 3,
          "circle-stroke-color": "#ffffff",
        },
      });

      setReady(true);
    };

    instance.on("load", buildLayers);

    // Switching theme replaces the basemap, and setStyle drops every custom
    // source and layer with it — so they have to be rebuilt on the far side.
    // Watching <html data-theme> rather than the media query catches an explicit
    // Light/Dark choice too, not just an OS change.
    const themeObserver = new MutationObserver(() => {
      const next = currentTheme();
      if (next === appliedTheme.current) return;
      appliedTheme.current = next;
      setReady(false);
      instance.setStyle(STYLE_URL[next]);
      instance.once("styledata", buildLayers);
    });
    themeObserver.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["data-theme"],
    });

    // MapLibre measures its container once at construction. Ours is inside a
    // flex/grid shell that settles AFTER that, so the canvas latches onto a
    // stale width (observed: 400px inside a 1038px shell) and never recovers.
    // A ResizeObserver is the only reliable fix — window resize alone misses
    // the initial settle and any layout change that does not resize the window.
    const observer = new ResizeObserver(() => instance.resize());
    if (container.current) observer.observe(container.current);

    map.current = instance;
    return () => {
      observer.disconnect();
      themeObserver.disconnect();
      instance.remove();
      map.current = null;
    };
    // Built once. Data and style changes are applied by the effects below rather
    // than by tearing the map down, which would lose the user's pan and zoom.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // --- data changes --------------------------------------------------------
  useEffect(() => {
    if (!ready || !map.current) return;
    const source = map.current.getSource("routes") as maplibregl.GeoJSONSource | undefined;
    source?.setData(geojson as never);
  }, [geojson, ready]);

  // --- visibility + mode ---------------------------------------------------
  useEffect(() => {
    if (!ready || !map.current) return;
    const m = map.current;
    m.setFilter("actual-lines", kindFilter("actual", visible));
    m.setFilter("optimal-lines", kindFilter("optimal", visible));
    m.setFilter("stops", kindFilter("stop", visible));
    m.setFilter("stop-labels", kindFilter("stop", visible));

    const showActual = mode === "actual" || mode === "split";
    const showOptimal = mode === "optimal" || mode === "split";
    m.setLayoutProperty("actual-lines", "visibility", showActual ? "visible" : "none");
    m.setLayoutProperty("optimal-lines", "visibility", showOptimal ? "visible" : "none");
    // Migration arrows belong to the fleet re-solve, so they follow the optimal
    // view rather than sitting on a plan they do not describe — and then only
    // when asked for. A fleet generates one arrow per reassigned stop, which on
    // a 6-route sample is 33 lines criss-crossing the map; drawn by default
    // they buried the two route lines the user opened "Both" to compare.
    m.setLayoutProperty(
      "migration-lines",
      "visibility",
      showOptimal && showMigrations ? "visible" : "none",
    );

    // The offset exists only to stop the two variants covering each other, so
    // it applies only when both are on screen. Viewed alone, a route must sit
    // on the road it actually drives — a permanent 2.5px shift would be a small
    // lie about where the vehicle goes, and at high zoom a visible one.
    const split = mode === "split";
    m.setPaintProperty("actual-lines", "line-offset", split ? ACTUAL_OFFSET_PX : 0);
    m.setPaintProperty("optimal-lines", "line-offset", split ? OPTIMAL_OFFSET_PX : 0);
  }, [visible, mode, showMigrations, ready]);

  // --- highlight -----------------------------------------------------------
  useEffect(() => {
    if (!ready || !map.current) return;
    const m = map.current;

    const focusRoutes = new Set<string>();
    if (selectedFindingId) {
      for (const f of geojson.features) {
        const ids = (f.properties as { finding_ids?: string[] }).finding_ids ?? [];
        if (ids.includes(selectedFindingId)) {
          const rid = (f.properties as { route_id?: string }).route_id;
          if (rid) focusRoutes.add(rid);
        }
      }
    } else if (selectedRouteId) {
      focusRoutes.add(selectedRouteId);
    }

    // Feature-state rather than a filter swap: dimming is a paint change, so it
    // costs nothing on a 5,000-stop fleet.
    geojson.features.forEach((f, i) => {
      const rid = (f.properties as { route_id?: string }).route_id;
      const dimmed = focusRoutes.size > 0 && (!rid || !focusRoutes.has(rid));
      const highlighted = focusRoutes.size > 0 && !!rid && focusRoutes.has(rid);
      m.setFeatureState({ source: "routes", id: i }, { dimmed, highlighted });
    });

    if (focusRoutes.size > 0) {
      const focused = geojson.features.filter((f) => {
        const rid = (f.properties as { route_id?: string }).route_id;
        return rid && focusRoutes.has(rid);
      });
      const box = bboxOf(focused);
      if (box) m.fitBounds(box, { padding: 80, maxZoom: 15, duration: 600 });
    } else if (geojson.bbox) {
      m.fitBounds(geojson.bbox, { padding: 48, duration: 600 });
    }
  }, [selectedFindingId, selectedRouteId, geojson, ready]);

  // --- interaction ---------------------------------------------------------
  const showPopup = useCallback((e: maplibregl.MapLayerMouseEvent) => {
    const feature = e.features?.[0];
    if (!feature || !map.current) return;
    popup.current?.remove();
    popup.current = new maplibregl.Popup({ closeButton: false, offset: 10 })
      .setLngLat(e.lngLat)
      .setHTML(stopTooltip(feature.properties as unknown as StopProps))
      .addTo(map.current);
  }, []);

  useEffect(() => {
    if (!ready || !map.current) return;
    const m = map.current;

    const onEnter = (e: maplibregl.MapLayerMouseEvent) => {
      m.getCanvas().style.cursor = "pointer";
      showPopup(e);
    };
    const onLeave = () => {
      m.getCanvas().style.cursor = "";
      popup.current?.remove();
      popup.current = null;
    };
    const onClickRoute = (e: maplibregl.MapLayerMouseEvent) => {
      const rid = e.features?.[0]?.properties?.route_id as string | undefined;
      if (rid) onSelectRoute(rid === selectedRouteId ? null : rid);
    };
    const onClickBackground = () => onSelectRoute(null);

    m.on("mouseenter", "stops", onEnter);
    m.on("mouseleave", "stops", onLeave);
    m.on("click", "stops", onClickRoute);
    m.on("click", "actual-lines", onClickRoute);
    m.on("click", onClickBackground);

    return () => {
      m.off("mouseenter", "stops", onEnter);
      m.off("mouseleave", "stops", onLeave);
      m.off("click", "stops", onClickRoute);
      m.off("click", "actual-lines", onClickRoute);
      m.off("click", onClickBackground);
    };
  }, [ready, showPopup, onSelectRoute, selectedRouteId]);

  return <div ref={container} className="map-canvas" data-testid="route-map" />;
}
