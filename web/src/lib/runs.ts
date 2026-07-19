/**
 * Local history of the runs started from this browser.
 *
 * There are no accounts, and a session is reachable only through its
 * unguessable link — which means the one and only way to lose a run is to lose
 * the URL, and that is exactly what happens when someone closes the tab while
 * a five-minute analysis is still going.
 *
 * So this is a convenience index, not an identity system. It lives entirely in
 * localStorage: nothing is sent to the server, the server could not answer
 * "which runs are mine" if asked, and clearing site data clears it. Two people
 * sharing a browser profile share this list, which is the same trade every
 * "recently opened" menu makes.
 *
 * Storing the id is not a new disclosure: the id is already in the URL bar and
 * in browser history. What it adds is a way back to it.
 */

const KEY = "routebench.runs.v1";
/** Enough to cover "the ones I might still want"; the server expires them at 72h anyway. */
const MAX_ENTRIES = 25;

export interface RunRecord {
  sessionId: string;
  /** ms since epoch, stamped client-side when the upload was accepted. */
  startedAt: number;
  /** Original filename, purely so the list is scannable. */
  filename?: string;
  /**
   * True once the run reached a terminal state (succeeded/failed/expired) in a
   * tab that was open to see it. Drives the resume banner: an undone run is one
   * the user may have navigated away from mid-flight and wants a way back to.
   *
   * It can be wrong in one direction — a run that finished in a tab the user
   * closed before it landed stays `undefined` here forever. That is deliberate:
   * the cost is the banner offering to "resume" a run that is actually done,
   * and clicking it simply shows the finished report. The opposite error —
   * hiding a genuinely in-progress run — is the one that stranded the user, so
   * this errs toward showing the link.
   */
  done?: boolean;
}

function read(): RunRecord[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = window.localStorage.getItem(KEY);
    if (!raw) return [];
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    // Filter rather than trust: this value is user-editable, may have been
    // written by an older version of this code, and is about to be used to
    // build links.
    return parsed.filter(
      (r): r is RunRecord =>
        !!r &&
        typeof r === "object" &&
        typeof (r as RunRecord).sessionId === "string" &&
        typeof (r as RunRecord).startedAt === "number",
    );
  } catch {
    // Private-mode Safari throws on localStorage, and a corrupt value should
    // not take the page down. No history is a degraded experience, not a
    // broken one.
    return [];
  }
}

function write(runs: RunRecord[]): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(KEY, JSON.stringify(runs.slice(0, MAX_ENTRIES)));
  } catch {
    // Quota exceeded or storage disabled. Losing the index is survivable.
  }
}

export function listRuns(): RunRecord[] {
  return read().sort((a, b) => b.startedAt - a.startedAt);
}

export function rememberRun(sessionId: string, filename?: string): void {
  const existing = read().filter((r) => r.sessionId !== sessionId);
  write([{ sessionId, startedAt: Date.now(), filename }, ...existing]);
}

/** Mark a run finished, so the resume banner stops offering it. Preserves the
 *  rest of the record. A no-op if the run is not known on this device. */
export function markRunDone(sessionId: string): void {
  const runs = read();
  const found = runs.find((r) => r.sessionId === sessionId);
  if (!found || found.done) return;
  write(runs.map((r) => (r.sessionId === sessionId ? { ...r, done: true } : r)));
}

/** The newest run not yet known to be finished, or null. What the resume banner
 *  offers a way back to. */
export function activeRun(): RunRecord | null {
  return listRuns().find((r) => !r.done) ?? null;
}

export function forgetRun(sessionId: string): void {
  write(read().filter((r) => r.sessionId !== sessionId));
}

export function forgetAllRuns(): void {
  write([]);
}
