"use client";

import { useCallback, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { MAX_UPLOAD_BYTES, formatBytes } from "@/lib/upload";

/**
 * The primary CTA: drop a file, go.
 *
 * Rejects what it can locally — wrong type, empty, over the cap — so a user
 * learns in a millisecond rather than after a round trip. The server validates
 * regardless; this is courtesy, not enforcement.
 *
 * Keyboard-operable: the zone is a real button, so Tab and Enter work without a
 * mouse. Drag-and-drop is an enhancement on top, never the only way in.
 */

export default function Dropzone() {
  const router = useRouter();
  const inputRef = useRef<HTMLInputElement>(null);
  const [isDragging, setDragging] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const accept = useCallback(
    (file: File | undefined) => {
      if (!file) return;
      setError(null);

      // The name is the reliable signal, not the MIME type. Windows reports
      // .csv files as application/vnd.ms-excel, so that type has to be allowed —
      // but allowing it on its own let a genuine .xlsx through, since the OS
      // reports those the same way. Require the extension, and treat the MIME
      // as corroboration for the rare file that has no extension at all.
      const name = file.name.toLowerCase();
      const isCsv = name.endsWith(".csv") || (!name.includes(".") && file.type === "text/csv");
      if (!isCsv) {
        setError(
          `${file.name} is not a CSV. Export your route plan as CSV and try again.`,
        );
        return;
      }
      if (file.size === 0) {
        setError(`${file.name} is empty.`);
        return;
      }
      if (file.size > MAX_UPLOAD_BYTES) {
        setError(
          `${file.name} is ${formatBytes(file.size)} — over the ${formatBytes(MAX_UPLOAD_BYTES)} limit.`,
        );
        return;
      }

      // Hand off to the upload flow, which parses headers, maps columns, and
      // collects analysis settings. The file itself cannot travel through a URL,
      // so it is staged in memory on the client.
      stageFile(file);
      router.push("/upload");
    },
    [router],
  );

  return (
    <div className="dropzone-wrap">
      <button
        type="button"
        className={`dropzone${isDragging ? " is-dragging" : ""}`}
        onClick={() => inputRef.current?.click()}
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          accept(e.dataTransfer.files[0]);
        }}
        aria-describedby="dropzone-hint"
      >
        <svg
          className="dropzone-icon"
          aria-hidden="true"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.5"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <path d="M12 16V4m0 0L8 8m4-4 4 4M4 16v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2" />
        </svg>
        <span className="dropzone-main">Drop your route plan here</span>
        <span className="dropzone-sub">or click to choose a file</span>
      </button>

      <input
        ref={inputRef}
        type="file"
        accept=".csv,text/csv"
        className="sr-only"
        onChange={(e) => accept(e.target.files?.[0])}
        aria-label="Route plan CSV"
      />

      <p id="dropzone-hint" className="dropzone-hint">
        CSV, up to {formatBytes(MAX_UPLOAD_BYTES)}.{" "}
        <a href="/api/template" download="routebench-template.csv">
          Download the template
        </a>{" "}
        or use your own column names — we&rsquo;ll map them.
      </p>

      {error ? (
        <p className="dropzone-error" role="alert">
          {error}
        </p>
      ) : null}
    </div>
  );
}

/**
 * Stage the chosen file for the upload flow.
 *
 * A File cannot cross a route change through the URL, and re-prompting on
 * /upload would make the landing dropzone a lie. Module scope rather than
 * sessionStorage: a File is not serialisable, and this only needs to survive a
 * client-side navigation, not a reload.
 */
let staged: File | null = null;

export function stageFile(file: File): void {
  staged = file;
}

export function takeStagedFile(): File | null {
  const file = staged;
  staged = null;
  return file;
}
