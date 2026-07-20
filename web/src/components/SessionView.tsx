"use client";

import dynamic from "next/dynamic";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import FindingsList from "@/components/FindingsList";
import Scorecard from "@/components/Scorecard";
import { SOLVER_DISCLAIMER } from "@/lib/constraints";
import { routeColor } from "@/lib/palette";
import { markRunDone } from "@/lib/runs";
import {
  ApiError,
  getAnalysis,
  getRoutesGeoJSON,
  getSession,
  reportUrl,
  subscribeToSession,
} from "@/lib/api";
import {
  TERMINAL_STATES,
  type AnalysisReport,
  type RoutesGeoJSON,
  type SessionStatus,
} from "@/lib/types";
import type { MapMode } from "@/components/RouteMap";

/**
 * A session: watch it run, then read the result.
 *
 * The map is loaded client-side only — MapLibre touches `window` at import time
 * and would break the server render.
 */
const RouteMap = dynamic(() => import("@/components/RouteMap"), {
  ssr: false,
  loading: () => <div className="map-canvas map-loading">Loading map…</div>,
});

/** SSE can die silently behind a proxy, so polling is the floor, not the plan. */
const POLL_MS = 3000;

const STAGE_LABEL: Record<string, string> = {
  queued: "Waiting for a slot",
  validating: "Reading your file",
  analyzing: "Scoring and re-solving",
  writing: "Writing the report",
  rendering: "Drawing the map",
  succeeded: "Done",
  failed: "Failed",
  expired: "Expired",
};

export default function SessionView({ sessionId }: { sessionId: string }) {
  const [status, setStatus] = useState<SessionStatus | null>(null);
  const [analysis, setAnalysis] = useState<AnalysisReport | null>(null);
  const [geojson, setGeojson] = useState<RoutesGeoJSON | null>(null);
  const [error, setError] = useState<Error | null>(null);

  const [mode, setMode] = useState<MapMode>("actual");
  const [selectedFindingId, setSelectedFindingId] = useState<string | null>(null);
  const [selectedRouteId, setSelectedRouteId] = useState<string | null>(null);
  const [hidden, setHidden] = useState<Set<string>>(new Set());
  // Reassignment arrows answer a different question than the plan-vs-solver
  // comparison — "which stops would move between routes" rather than "how does
  // my order compare". On a 6-route fleet there are 33 of them, and drawn by
  // default they buried the two route lines the user came to compare. Off
  // until asked for.
  const [showMigrations, setShowMigrations] = useState(false);

  const done = status !== null && TERMINAL_STATES.has(status.state);

  // Once this run lands, stop the resume banner offering it. Guarded by `done`
  // so it fires once per terminal transition, not on every poll.
  useEffect(() => {
    if (done) markRunDone(sessionId);
  }, [done, sessionId]);

  // --- follow the session --------------------------------------------------
  useEffect(() => {
    let cancelled = false;

    const apply = (next: SessionStatus) => {
      if (!cancelled) setStatus(next);
    };

    getSession(sessionId).then(apply).catch((e) => !cancelled && setError(e));
    const unsubscribe = subscribeToSession(sessionId, apply);

    // Belt and braces: SSE closes after 11 minutes server-side and can be eaten
    // by a proxy without either end noticing.
    const poll = setInterval(() => {
      getSession(sessionId)
        .then((next) => {
          apply(next);
          if (TERMINAL_STATES.has(next.state)) clearInterval(poll);
        })
        .catch(() => {});
    }, POLL_MS);

    return () => {
      cancelled = true;
      unsubscribe();
      clearInterval(poll);
    };
  }, [sessionId]);

  // --- load artifacts once it lands ---------------------------------------
  useEffect(() => {
    if (status?.state !== "succeeded" || analysis) return;
    Promise.all([getAnalysis(sessionId), getRoutesGeoJSON(sessionId)])
      .then(([a, g]) => {
        setAnalysis(a);
        setGeojson(g);
      })
      .catch(setError);
  }, [status?.state, sessionId, analysis]);

  const routeIds = useMemo(
    () => (analysis ? analysis.fleet.routes.map((r) => r.route_id).sort() : []),
    [analysis],
  );
  const visibleRoutes = useMemo(
    () => new Set(routeIds.filter((id) => !hidden.has(id))),
    [routeIds, hidden],
  );

  const toggleRoute = useCallback((routeId: string) => {
    setHidden((prev) => {
      const next = new Set(prev);
      if (next.has(routeId)) next.delete(routeId);
      else next.add(routeId);
      return next;
    });
  }, []);

  // Selecting a finding and selecting a route are two views of one selection;
  // letting both be set at once would leave the map and list disagreeing.
  const selectFinding = useCallback((id: string | null) => {
    setSelectedFindingId(id);
    setSelectedRouteId(null);
  }, []);
  const selectRoute = useCallback((id: string | null) => {
    setSelectedRouteId(id);
    setSelectedFindingId(null);
  }, []);

  if (error) return <Failure error={error} />;
  if (!status) return <Waiting label="Finding your session…" />;
  if (!done) return <Progress status={status} />;
  if (status.state === "failed") return <FailedSession status={status} />;
  if (status.state === "expired") return <ExpiredSession />;
  if (!analysis || !geojson) return <Waiting label="Loading your results…" />;

  const hasOptimal = geojson.features.some((f) => f.properties.kind === "optimal");
  const hasMigrations = geojson.features.some((f) => f.properties.kind === "migration");

  return (
    <div className="container results">
      {analysis.grade ? (
        <>
          <h1>Your route quality score</h1>
          <Scorecard
            grade={analysis.grade}
            caption={`${analysis.fleet_metrics.total_routes} routes · ${analysis.fleet_metrics.total_stops} stops`}
          />
          {analysis.benchmark?.fleet_level ? (
            <p className="headline-gap">
              {analysis.benchmark.fleet_level.improvement_gap_pct > 0 ? (
                <>
                  The solver found a fleet plan{" "}
                  <strong>
                    {analysis.benchmark.fleet_level.improvement_gap_pct.toFixed(1)}% shorter
                  </strong>{" "}
                  than yours — and that is a floor, not a ceiling.
                </>
              ) : (
                <>
                  <strong>Your plan is within solver reach</strong> — no material cross-route
                  savings were found.
                </>
              )}
            </p>
          ) : null}
        </>
      ) : (
        <>
          <h1>Your results</h1>
          {/* A missing score with no explanation reads as a broken page. When
              the grade was withheld on purpose, say so and say why — and say the
              TRUE why: a routing outage and a hit spend-cap are different, and
              claiming "the routing service was unavailable" when it was actually
              a daily limit would be a small lie in a tool whose whole point is
              not telling you things it cannot support. */}
          {analysis.matrix_approximate ? (
            <p className="grade-withheld" role="note">
              <strong>Quality score withheld.</strong>{" "}
              {analysis.matrix_approximate_reason === "budget" ? (
                <>
                  Today&rsquo;s analysis limit was reached, so travel times below are
                  estimated from straight-line distance rather than live routing. Your
                  routes, stops, and findings still hold, but a score computed from
                  estimated times would look more precise than it is. Try again after
                  midnight UTC, when the limit resets.
                </>
              ) : (
                <>
                  The routing service was unavailable, so travel times below are
                  estimated from straight-line distance rather than real road networks.
                  Your routes, stops, and findings still hold, but a score computed from
                  estimated times would look more precise than it is. Re-run once routing
                  is back to get a graded result.
                </>
              )}
            </p>
          ) : null}
        </>
      )}

      {/* Independent of the grade: the evaluation is complete either way, only
          the writing changes. `=== false` rather than `!` so an older artifact
          without the field does not show the notice. */}
      {analysis.llm_assisted === false ? (
        <p className="prose-templated" role="note">
          <strong>Written summaries are off for this run.</strong> The
          explanations below are filled from templates rather than written out.
          Your score, findings, benchmark and map are unaffected — none of them
          involve a language model, and every available check was run rather
          than a selected subset.
        </p>
      ) : null}

      {geojson.properties.geometry_quality === "approximate" ? (
        <p className="geometry-warning" role="note">
          {/* The artifact's own note already says what happened and why; adding
              our own sentence in front of it just says it twice. */}
          {geojson.properties.geometry_note}
        </p>
      ) : null}

      <div className="map-shell">
        <div className="map-toolbar">
          <div className="map-modes" role="group" aria-label="Map view">
            {(["actual", "optimal", "split"] as MapMode[]).map((m) => (
              <button
                key={m}
                type="button"
                className={mode === m ? "is-active" : ""}
                disabled={m !== "actual" && !hasOptimal}
                onClick={() => setMode(m)}
                title={
                  m !== "actual" && !hasOptimal
                    ? "No benchmark ran for this fleet"
                    : undefined
                }
              >
                {m === "actual" ? "Your plan" : m === "optimal" ? "Solver" : "Both"}
              </button>
            ))}
          </div>

          {hasMigrations && mode !== "actual" ? (
            <label className="map-toggle">
              <input
                type="checkbox"
                checked={showMigrations}
                onChange={() => setShowMigrations((v) => !v)}
              />
              Stop reassignments
            </label>
          ) : null}

          <details className="route-toggles">
            <summary>
              Routes ({visibleRoutes.size}/{routeIds.length})
            </summary>
            <div className="route-toggle-actions">
              <button type="button" className="btn-link" onClick={() => setHidden(new Set())}>
                All
              </button>
              <button
                type="button"
                className="btn-link"
                onClick={() => setHidden(new Set(routeIds))}
              >
                None
              </button>
            </div>
            <ul>
              {routeIds.map((id) => (
                <li key={id}>
                  <label>
                    <input
                      type="checkbox"
                      checked={!hidden.has(id)}
                      onChange={() => toggleRoute(id)}
                    />
                    <span
                      className="route-swatch"
                      style={{ background: routeColor(routeIds, id) }}
                      aria-hidden="true"
                    />
                    {id}
                  </label>
                </li>
              ))}
            </ul>
          </details>
        </div>

        {/*
          Only in "Both": with one variant on screen there is nothing to tell
          apart, and a legend explaining a distinction the user cannot currently
          see is noise. This is also the only place the dashed/solid convention
          is ever stated — before this, nothing on the page said what the second
          line was.
        */}
        {mode === "split" ? (
          <p className="map-legend">
            <span className="legend-item">
              <span className="legend-line legend-line-plan" aria-hidden="true" />
              Your plan
            </span>
            <span className="legend-item">
              <span className="legend-line legend-line-solver" aria-hidden="true" />
              Solver&rsquo;s order
            </span>
            {showMigrations ? (
              <span className="legend-item">
                <span className="legend-line legend-line-migration" aria-hidden="true" />
                Stop moves to another route
              </span>
            ) : null}
            <span className="legend-note">
              Both lines follow each route&rsquo;s own colour. Where the two orders drive the
              same road they are drawn side by side rather than on top of each other.
            </span>
          </p>
        ) : null}

        <RouteMap
          geojson={geojson}
          routeIds={routeIds}
          visibleRoutes={visibleRoutes}
          mode={mode}
          showMigrations={showMigrations}
          selectedFindingId={selectedFindingId}
          selectedRouteId={selectedRouteId}
          onSelectRoute={selectRoute}
        />
      </div>

      <FindingsList
        findings={analysis.findings}
        selectedFindingId={selectedFindingId}
        onSelectFinding={selectFinding}
        filterRouteId={selectedRouteId}
        onClearFilter={() => setSelectedRouteId(null)}
      />

      <div className="downloads">
        <h2 className="section-title">Take it with you</h2>
        <a className="btn-primary" href={reportUrl(sessionId, "html")} target="_blank" rel="noopener noreferrer">
          Full report (HTML)
        </a>
        {status.artifacts?.report_pdf ? (
          <a className="btn-secondary" href={reportUrl(sessionId, "pdf")}>
            PDF
          </a>
        ) : null}
        <p className="dim-note">
          This page is reachable at its link for as long as the session is kept. There is no
          account — save the URL if you want to come back.
        </p>
      </div>

      <p className="disclaimer" role="note">
        {SOLVER_DISCLAIMER}
      </p>
    </div>
  );
}

function Waiting({ label }: { label: string }) {
  return (
    <div className="container">
      <p className="lede">{label}</p>
    </div>
  );
}

/**
 * Counts the solver budget down between polls.
 *
 * The server only speaks every few seconds, so without a local tick the number
 * would sit still and then jump — which reads as a stall during the exact phase
 * this exists to make legible. Resets whenever the server sends a new figure,
 * so the server always wins and local drift cannot accumulate.
 */
function useCountdown(seconds: number | null): number | null {
  const [remaining, setRemaining] = useState<number | null>(seconds);

  useEffect(() => {
    setRemaining(seconds);
    if (seconds === null) return;
    const id = setInterval(() => {
      // Floor at zero rather than going negative: the budget is a ceiling the
      // solver may finish under, so "0s" honestly means "any moment now".
      setRemaining((r) => (r === null ? null : Math.max(0, r - 1)));
    }, 1000);
    return () => clearInterval(id);
  }, [seconds]);

  return remaining;
}

function formatDuration(seconds: number): string {
  if (seconds < 60) return `${seconds}s`;
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return s === 0 ? `${m}m` : `${m}m ${s}s`;
}

function Progress({ status }: { status: SessionStatus }) {
  const remaining = useCountdown(status.seconds_remaining ?? null);

  return (
    <div className="container progress-page">
      <h1>Analysing your routes</h1>
      <p className="lede">{STAGE_LABEL[status.state] ?? status.state}</p>

      <div
        className="progress-bar"
        role="progressbar"
        aria-valuenow={status.progress_pct}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label="Analysis progress"
      >
        <span style={{ width: `${Math.max(2, status.progress_pct)}%` }} />
      </div>

      <p className="progress-detail">
        {status.stage_detail}
        {remaining !== null ? (
          // "at most", because the figure is a solver budget rather than a
          // prediction — the run can finish early but will not overrun it.
          <span className="progress-eta">
            {" · "}
            {remaining > 0 ? `at most ${formatDuration(remaining)} left` : "finishing up"}
          </span>
        ) : null}
      </p>
      <p className="dim-note">
        This page keeps itself up to date. The link is yours — you can close the tab and come
        back to it.
      </p>
    </div>
  );
}

/** Each failure code gets its own explanation; a generic error helps nobody. */
function FailedSession({ status }: { status: SessionStatus }) {
  const code = status.error?.code ?? "";
  const known: Record<string, { title: string; body: React.ReactNode }> = {
    interrupted_by_restart: {
      title: "We restarted mid-analysis",
      body: (
        <>
          Your upload was preserved, but the run did not finish. Nothing is wrong with your
          file — please upload it again.
        </>
      ),
    },
    stale: {
      title: "The analysis stopped responding",
      body: (
        <>
          It ran too long without progress and we stopped it. Your upload was preserved;
          uploading again is the fastest fix.
        </>
      ),
    },
    JOB_TIMEOUT: {
      title: "The analysis ran out of time",
      body: (
        <>
          This usually means a very large fleet. Try again, or split the plan into smaller
          uploads.
        </>
      ),
    },
  };

  const detail = known[code];

  return (
    <div className="container">
      <h1>{detail?.title ?? "The analysis failed"}</h1>
      <p className="lede">{detail?.body ?? status.error?.message ?? status.stage_detail}</p>
      {code ? (
        <p className="dim-note">
          Support ID: <code>{status.session_id}</code> · <code>{code}</code>
        </p>
      ) : null}
      <p>
        <Link href="/">← Try again</Link>
      </p>
    </div>
  );
}

function ExpiredSession() {
  return (
    <div className="container">
      <h1>This session has expired</h1>
      <p className="lede">
        Reports are kept for a limited time and then deleted. Upload your plan again to get a
        fresh one.
      </p>
      <p>
        <Link href="/">← Start over</Link>
      </p>
    </div>
  );
}

function Failure({ error }: { error: Error }) {
  const notFound = error instanceof ApiError && error.status === 404;
  return (
    <div className="container">
      <h1>{notFound ? "No such session" : "Something went wrong"}</h1>
      <p className="lede">
        {notFound
          ? "That link does not match a session we hold. It may have expired, or the URL may be incomplete."
          : error.message}
      </p>
      <p>
        <Link href="/">← Start over</Link>
      </p>
    </div>
  );
}
