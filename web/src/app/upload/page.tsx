"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import Papa from "papaparse";
import ColumnMapper, {
  missingRequired,
  suggestMapping,
  type Mapping,
} from "@/components/ColumnMapper";
import ConstraintsPanel from "@/components/ConstraintsPanel";
import { takeStagedFile } from "@/components/Dropzone";
import RowPreview from "@/components/RowPreview";
import { ApiError, createSession } from "@/lib/api";
import { DEFAULT_PANEL, buildConfig, type PanelState } from "@/lib/config-builder";
import { headersMatchSchema } from "@/lib/schema";
import { formatBytes } from "@/lib/upload";

/**
 * Upload: confirm the file, then go.
 *
 * The mapping step appears only when it must — a standard file skips straight to
 * confirm, which is the point of "one decision per screen".
 *
 * Everything here is client-side and deterministic. Headers are rewritten to the
 * standard form before upload so the server sees a file it recognises; the
 * server still validates, and its 422 is anchored back to the user's own rows.
 */

const PREVIEW_ROWS = 20;

interface ParsedFile {
  headers: string[];
  rows: Record<string, string>[];
  /** Rows beyond the preview are never parsed; this is what we sampled. */
  sampledRows: number;
}

export default function UploadPage() {
  const router = useRouter();
  const [file, setFile] = useState<File | null>(null);
  const [parsed, setParsed] = useState<ParsedFile | null>(null);
  const [parseError, setParseError] = useState<string | null>(null);
  const [mapping, setMapping] = useState<Mapping>({});
  const [panel, setPanel] = useState<PanelState>(DEFAULT_PANEL);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<ApiError | Error | null>(null);

  useEffect(() => {
    const staged = takeStagedFile();
    setFile(staged);
    if (!staged) return;

    // Parse only the head of the file: a 50 MB upload should not be read whole
    // in the browser to show 20 rows and a set of column names.
    Papa.parse<Record<string, string>>(staged, {
      header: true,
      skipEmptyLines: true,
      preview: PREVIEW_ROWS,
      complete: (result) => {
        const headers = (result.meta.fields ?? []).filter((h) => h.trim() !== "");
        if (headers.length === 0) {
          setParseError("We could not read a header row. Is this a CSV?");
          return;
        }
        setParsed({ headers, rows: result.data, sampledRows: result.data.length });
        setMapping(suggestMapping(headers));
      },
      error: (err: Error) => setParseError(err.message),
    });
  }, []);

  const needsMapping = useMemo(
    () => (parsed ? !headersMatchSchema(parsed.headers) : false),
    [parsed],
  );
  const missing = useMemo(() => missingRequired(mapping), [mapping]);
  const canSubmit = file !== null && parsed !== null && missing.length === 0 && !submitting;

  const submit = useCallback(async () => {
    if (!file || !parsed) return;
    setSubmitting(true);
    setSubmitError(null);
    try {
      const prepared = await rewriteHeaders(file, mapping);
      const { session_id } = await createSession(prepared, buildConfig(panel));
      router.push(`/s/${session_id}`);
    } catch (err) {
      setSubmitError(err instanceof Error ? err : new Error(String(err)));
      setSubmitting(false);
    }
  }, [file, parsed, mapping, panel, router]);

  if (!file) {
    return (
      <div className="container">
        <h1>No file chosen</h1>
        <p className="lede">
          Your file did not make it here — that usually means the page was reloaded.
        </p>
        <p>
          <Link href="/">← Start over</Link>
        </p>
      </div>
    );
  }

  return (
    <div className="container upload-page">
      <h1>{needsMapping ? "Check your columns" : "Ready to analyse"}</h1>
      <p className="lede">
        {file.name} · {formatBytes(file.size)}
      </p>

      {parseError ? (
        <p className="dropzone-error" role="alert">
          {parseError} <Link href="/">Choose a different file</Link>
        </p>
      ) : null}

      {parsed ? (
        <>
          {needsMapping ? (
            <>
              <p>
                Your columns don&rsquo;t use our names, which is fine — here is what we think
                they mean. Correct anything we got wrong.
              </p>
              <ColumnMapper headers={parsed.headers} mapping={mapping} onChange={setMapping} />
            </>
          ) : (
            <p className="mapper-ok">Your columns match the RouteBench format — nothing to map.</p>
          )}

          <h2 className="section-title">Preview</h2>
          <p className="section-lede">
            The first {parsed.sampledRows} row{parsed.sampledRows === 1 ? "" : "s"} of your file,
            as we read them.
          </p>
          <RowPreview headers={parsed.headers} rows={parsed.rows} mapping={mapping} />

          <ConstraintsPanel
            panel={panel}
            onChange={setPanel}
            open={settingsOpen}
            onOpenChange={setSettingsOpen}
          />

          {submitError ? <SubmitError error={submitError} /> : null}

          <div className="submit-row">
            <button type="button" className="btn-primary" onClick={submit} disabled={!canSubmit}>
              {submitting ? "Uploading…" : "Analyse my routes"}
            </button>
            {missing.length > 0 ? (
              <span className="submit-blocked">Map {missing.join(", ")} first.</span>
            ) : null}
            <Link href="/" className="submit-cancel">
              Choose a different file
            </Link>
          </div>
        </>
      ) : parseError ? null : (
        <p>Reading your file…</p>
      )}
    </div>
  );
}

/** The API's status IS the meaning; each one gets its own explanation. */
function SubmitError({ error }: { error: ApiError | Error }) {
  if (!(error instanceof ApiError)) {
    return (
      <p className="dropzone-error" role="alert">
        We could not reach the analysis service. Check your connection and try again.
      </p>
    );
  }

  if (error.status === 422 && error.validationErrors.length > 0) {
    return (
      <div className="dropzone-error" role="alert">
        <p>Your file was rejected:</p>
        <ul>
          {error.validationErrors.map((v, i) => (
            <li key={i}>
              {v.row != null ? <strong>Row {v.row}: </strong> : null}
              {v.message}
            </li>
          ))}
        </ul>
      </div>
    );
  }

  const message: Record<number, string> = {
    413: "That file is too large.",
    422: `We could not read that file. ${error.message}`,
    429: "We are at capacity right now. Try again in a few minutes.",
    503: "We have hit today's analysis budget. Service resumes at UTC midnight.",
  };

  return (
    <p className="dropzone-error" role="alert">
      {message[error.status] ?? `Something went wrong (${error.status}). ${error.message}`}
    </p>
  );
}

/**
 * Rewrite the file's headers to the standard names.
 *
 * The whole file is read here — the server needs every row, not the preview —
 * but only once, and only after the user has committed to uploading.
 *
 * Values are passed through as raw strings: a value that round-trips untouched
 * is a value the server sees exactly as the user wrote it, which keeps the
 * client from silently "fixing" a coordinate on the way past.
 */
async function rewriteHeaders(file: File, mapping: Mapping): Promise<File> {
  const text = await file.text();
  const parsed = Papa.parse<string[]>(text, { skipEmptyLines: true });
  const rows = parsed.data;
  if (rows.length === 0) return file;

  const original = rows[0] ?? [];
  const renamed = original.map((header) => mapping[header] ?? header);

  // Drop columns mapped to nothing: an unmapped column is one the user chose to
  // ignore, and passing it through would invite the server to guess.
  const keep = original
    .map((header, i) => (mapping[header] === null ? -1 : i))
    .filter((i) => i >= 0);

  const out = [
    keep.map((i) => renamed[i] ?? ""),
    ...rows.slice(1).map((row) => keep.map((i) => row[i] ?? "")),
  ];

  return new File([Papa.unparse(out)], file.name, { type: "text/csv" });
}
