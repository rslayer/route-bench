"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { takeStagedFile } from "@/components/Dropzone";
import { formatBytes } from "@/lib/upload";
import { headersMatchSchema } from "@/lib/schema";

/**
 * Upload flow — landing stage.
 *
 * The column mapper, row preview, and constraints panel land in the next slice.
 * For now this proves the handoff: the file chosen on the landing page arrives
 * here intact, so the dropzone is a real CTA rather than a decoration.
 */
export default function UploadPage() {
  const [file, setFile] = useState<File | null>(null);
  const [headers, setHeaders] = useState<string[] | null>(null);

  useEffect(() => {
    const staged = takeStagedFile();
    setFile(staged);
    if (!staged) return;
    // Peek at the header row only — a 50 MB file should not be read whole just
    // to learn its column names.
    staged.slice(0, 64 * 1024).text().then((chunk) => {
      const firstLine = chunk.split(/\r?\n/)[0] ?? "";
      setHeaders(firstLine.split(",").map((h) => h.trim().replace(/^"|"$/g, "")));
    });
  }, []);

  if (!file) {
    return (
      <div className="container">
        <h1>No file chosen</h1>
        <p className="lede">
          Your file did not make it here — that usually means the page was
          reloaded.
        </p>
        <p>
          <Link href="/">← Start over</Link>
        </p>
      </div>
    );
  }

  return (
    <div className="container">
      <h1>Check your columns</h1>
      <p className="lede">
        {file.name} · {formatBytes(file.size)}
      </p>

      {headers ? (
        <>
          <p>
            {headersMatchSchema(headers)
              ? "These columns match the RouteBench format."
              : "We will map these to RouteBench fields in the next step."}
          </p>
          <ul className="header-list">
            {headers.map((h) => (
              <li key={h}>
                <code>{h}</code>
              </li>
            ))}
          </ul>
        </>
      ) : (
        <p>Reading columns…</p>
      )}

      <p className="scaffold-note">
        The column mapper, row preview, and analysis settings arrive in the next
        slice.
      </p>
      <p>
        <Link href="/">← Choose a different file</Link>
      </p>
    </div>
  );
}
