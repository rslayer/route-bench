/**
 * Site footer (Task 6). On every page.
 *
 * The version is fetched from the API's /health rather than baked into this
 * build, deliberately: the number that matters is the version of the service
 * that analyzed your routes, not the version of the page you happen to be
 * looking at. The two deploy separately and will drift.
 */

import { API_BASE } from "@/lib/api";
import type { BuildInfo } from "@/lib/types";

export const CONTACT_EMAIL = "ali@alikamil.com";
export const REPO_URL = "https://github.com/rslayer/route-bench";

async function fetchBuildInfo(): Promise<BuildInfo | null> {
  try {
    const response = await fetch(`${API_BASE}/health`, {
      // Rendered on the server per request; a cached version would go stale the
      // moment the API redeploys.
      cache: "no-store",
      signal: AbortSignal.timeout(3000),
    });
    if (!response.ok) return null;
    return (await response.json()) as BuildInfo;
  } catch {
    // The footer must render even when the API is down or slow. A missing
    // version is a cosmetic gap; a footer that throws takes the page with it.
    return null;
  }
}

function GitHubIcon() {
  return (
    <svg
      aria-hidden="true"
      width="16"
      height="16"
      viewBox="0 0 16 16"
      fill="currentColor"
      style={{ verticalAlign: "text-bottom" }}
    >
      <path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27s1.36.09 2 .27c1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.01 8.01 0 0 0 16 8c0-4.42-3.58-8-8-8Z" />
    </svg>
  );
}

export default async function Footer() {
  const build = await fetchBuildInfo();

  return (
    <footer className="footer">
      <div className="footer-inner">
        <p className="footer-care">
          RouteBench is an independent benchmarking tool. It is not affiliated
          with any routing vendor.
        </p>

        <nav className="footer-links" aria-label="Footer">
          <a href={REPO_URL} target="_blank" rel="noopener noreferrer">
            <GitHubIcon /> Source &amp; methodology
          </a>
          <span className="footer-sep" aria-hidden="true">
            ·
          </span>
          <a href="/privacy">Privacy</a>
          <span className="footer-sep" aria-hidden="true">
            ·
          </span>
          <span>
            Source-available under{" "}
            <a
              href={`${REPO_URL}/blob/main/LICENSE.md`}
              target="_blank"
              rel="noopener noreferrer"
            >
              FSL-1.1-ALv2
            </a>
          </span>
        </nav>

        <p className="footer-meta">
          Built by Ali Kamil ·{" "}
          <a href={`mailto:${CONTACT_EMAIL}`}>{CONTACT_EMAIL}</a>
          {build ? (
            <>
              {" · "}
              <span className="footer-version" title="Analysis service version">
                v{build.version}
                {build.commit !== "unknown" ? ` · build ${build.commit}` : ""}
              </span>
            </>
          ) : null}
        </p>
      </div>
    </footer>
  );
}
