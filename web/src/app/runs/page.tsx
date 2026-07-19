"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { forgetAllRuns, forgetRun, listRuns, type RunRecord } from "@/lib/runs";

/**
 * Runs started from this browser.
 *
 * Client-only and read after mount rather than during render: localStorage does
 * not exist on the server, and rendering an empty list on the server and a full
 * one on the client is a hydration mismatch. The brief empty state is honest —
 * we genuinely do not know what is in storage until we are in the browser.
 */
export default function RunsPage() {
  const [runs, setRuns] = useState<RunRecord[] | null>(null);

  useEffect(() => {
    setRuns(listRuns());
  }, []);

  const drop = (sessionId: string) => {
    forgetRun(sessionId);
    setRuns(listRuns());
  };

  const dropAll = () => {
    forgetAllRuns();
    setRuns(listRuns());
  };

  return (
    <div className="container prose-page">
      <h1>Your runs</h1>
      <p className="lede">
        Analyses started from this browser. RouteBench has no accounts, so this list lives
        on this device only — it is not sent to us, and we could not look it up for you.
      </p>

      {runs === null ? (
        <p className="dim-note">Checking this browser…</p>
      ) : runs.length === 0 ? (
        <p className="callout">
          Nothing here yet. <Link href="/upload">Upload a route plan</Link> and it will
          appear.
        </p>
      ) : (
        <>
          <ul className="run-list">
            {runs.map((run) => (
              <li key={run.sessionId}>
                <Link href={`/s/${run.sessionId}`}>
                  <span className="run-name">{run.filename ?? "Route plan"}</span>
                  <span className="run-meta">
                    {new Date(run.startedAt).toLocaleString()} ·{" "}
                    <code>{run.sessionId.slice(0, 8)}</code>
                  </span>
                </Link>
                <button
                  type="button"
                  className="btn-link"
                  onClick={() => drop(run.sessionId)}
                  aria-label={`Remove ${run.filename ?? run.sessionId} from this list`}
                >
                  Remove
                </button>
              </li>
            ))}
          </ul>
          <p className="run-actions">
            <button type="button" className="btn-link" onClick={dropAll}>
              Clear this list
            </button>
          </p>
        </>
      )}

      {/* Said plainly because "Remove" is ambiguous on a page about stored data,
          and the honest answer is that this button does less than it looks like. */}
      <p className="dim-note">
        Removing an entry forgets the link on this device. It does not delete the
        analysis — uploads and reports are deleted on their own schedule, described in
        the <Link href="/privacy">privacy note</Link>. Runs older than 72 hours will
        already have expired server-side.
      </p>
    </div>
  );
}
