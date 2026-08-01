/**
 * Client for the RouteBench API.
 *
 * The API is a separate origin (CORS allowlist via WEB_ORIGIN), so every call
 * is absolute against NEXT_PUBLIC_API_BASE.
 *
 * Errors are surfaced as ApiError with the HTTP status intact, because the
 * status *is* the meaning here: 422 carries validation errors to anchor against
 * the user's file, 429 means the queue is full, 503 means the daily budget
 * tripped. The UI renders a different screen for each, so swallowing the status
 * would flatten them into one useless "something went wrong".
 */

import type {
  AnalysisConfig,
  AnalysisReport,
  BuildInfo,
  IndustryProfile,
  RoutesGeoJSON,
  SessionStatus,
} from "./types";

export const API_BASE = (
  process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000"
).replace(/\/$/, "");

export interface ValidationErrorDetail {
  code: string;
  message: string;
  row?: number | null;
  column?: string | null;
}

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
    readonly detail?: unknown,
  ) {
    super(message);
    this.name = "ApiError";
  }

  /** Row/column-anchored errors from a rejected CSV, when the status is 422. */
  get validationErrors(): ValidationErrorDetail[] {
    const detail = this.detail as
      | { validation_errors?: ValidationErrorDetail[] }
      | undefined;
    return detail?.validation_errors ?? [];
  }
}

async function toApiError(response: Response): Promise<ApiError> {
  let detail: unknown;
  let message = `Request failed (${response.status})`;
  try {
    const body = await response.json();
    detail = body?.detail ?? body;
    if (typeof detail === "string") message = detail;
  } catch {
    // A non-JSON error body (a proxy's HTML 502, say) still has a usable status.
  }
  return new ApiError(response.status, message, detail);
}

async function getJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: { Accept: "application/json", ...init?.headers },
  });
  if (!response.ok) throw await toApiError(response);
  return (await response.json()) as T;
}

export interface CreateSessionResponse {
  session_id: string;
  status_url: string;
}

/**
 * Upload a CSV and start an analysis.
 *
 * `config` is sent verbatim as the constraints panel produced it — the promise
 * is that what the user saw is what runs, so this must not inject defaults of
 * its own.
 */
export async function createSession(
  file: File,
  config: AnalysisConfig,
): Promise<CreateSessionResponse> {
  const form = new FormData();
  form.append("file", file, file.name);
  form.append("config", JSON.stringify(config));

  const response = await fetch(`${API_BASE}/sessions`, {
    method: "POST",
    body: form,
  });
  if (!response.ok) throw await toApiError(response);
  return (await response.json()) as CreateSessionResponse;
}

export function getSession(sessionId: string): Promise<SessionStatus> {
  // Sessions change while in flight; a cached status would stall the progress UI.
  return getJson<SessionStatus>(`/sessions/${sessionId}`, { cache: "no-store" });
}

export function getAnalysis(sessionId: string): Promise<AnalysisReport> {
  return getJson<AnalysisReport>(`/sessions/${sessionId}/analysis.json`);
}

export function getRoutesGeoJSON(sessionId: string): Promise<RoutesGeoJSON> {
  return getJson<RoutesGeoJSON>(`/sessions/${sessionId}/routes.geojson`);
}

export function getBuildInfo(): Promise<BuildInfo> {
  return getJson<BuildInfo>("/health");
}

export function getIndustryProfiles(): Promise<IndustryProfile[]> {
  return getJson<IndustryProfile[]>("/industry-profiles");
}

export function reportUrl(sessionId: string, kind: "html" | "pdf"): string {
  return `${API_BASE}/sessions/${sessionId}/report.${kind}`;
}

/**
 * Subscribe to a session's progress over SSE.
 *
 * Returns an unsubscribe function. The caller still needs a polling fallback:
 * SSE dies silently behind some proxies, and the server closes the stream after
 * 11 minutes regardless.
 */
export function subscribeToSession(
  sessionId: string,
  onProgress: (status: SessionStatus) => void,
  onError?: (error: Error) => void,
): () => void {
  const source = new EventSource(`${API_BASE}/sessions/${sessionId}/events`);

  const handle = (event: MessageEvent<string>) => {
    try {
      onProgress(JSON.parse(event.data) as SessionStatus);
    } catch {
      // A malformed frame is not worth tearing the stream down for; the next
      // one, or the polling fallback, will carry the state.
    }
  };

  source.addEventListener("progress", handle);
  source.addEventListener("complete", (event) => {
    handle(event as MessageEvent<string>);
    source.close();
  });
  source.addEventListener("error", () => {
    // EventSource reconnects on its own; only report once it has given up.
    if (source.readyState === EventSource.CLOSED) {
      onError?.(new Error("Progress stream closed"));
    }
  });

  return () => source.close();
}
