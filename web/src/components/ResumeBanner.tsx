"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { activeRun, type RunRecord } from "@/lib/runs";

/**
 * A persistent way back to a run you navigated away from.
 *
 * An analysis takes minutes and outlives the tab, so it is normal to click
 * elsewhere while it runs — and until now the only way back was browser
 * history, because the run lives at an unguessable URL with nothing linking to
 * it. This banner rides along on every page and points at the most recent
 * unfinished run.
 *
 * It hides itself on that run's own page (you are already there) and re-checks
 * on navigation, since a route change is exactly when "am I still on the run?"
 * and "did another tab finish it?" can both have changed. Client-only: the
 * source of truth is localStorage, which does not exist on the server, so it
 * renders nothing until mounted rather than risk a hydration mismatch.
 */
export default function ResumeBanner() {
  const pathname = usePathname();
  const [run, setRun] = useState<RunRecord | null>(null);

  useEffect(() => {
    // SSR-safe hydration read: the active run comes from localStorage, so state
    // starts null and is resolved on mount and whenever the route changes.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setRun(activeRun());
  }, [pathname]);

  if (!run) return null;
  // Already looking at it — a "resume" link to the current page is noise.
  if (pathname === `/s/${run.sessionId}`) return null;

  return (
    <div className="resume-banner" role="status">
      <span className="resume-dot" aria-hidden="true" />
      <span className="resume-text">
        An analysis{run.filename ? ` of ${run.filename}` : ""} is in progress.
      </span>
      <Link href={`/s/${run.sessionId}`} className="resume-link">
        View it
      </Link>
    </div>
  );
}
