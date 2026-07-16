"use client";

import type { Mapping } from "./ColumnMapper";

/**
 * The first rows of the file, as we read them.
 *
 * The per-cell hints catch the mistakes that are obvious locally and expensive
 * remotely: a latitude in the longitude column, a stop_sequence that is not a
 * number, a missing required value. They are hints, not gates — the server is
 * the authority, and a hint that blocked an upload the server would have
 * accepted would be worse than none.
 *
 * The depot row (stop_sequence = 0) is called out because its absence is the
 * single most common reason a first upload is rejected, and it is far cheaper to
 * notice here than in a 422.
 */

type CellIssue = { level: "error" | "warn"; note: string } | null;

function checkCell(field: string | null, raw: string): CellIssue {
  if (!field) return null;
  const value = raw?.trim() ?? "";

  const required = ["route_id", "stop_sequence", "latitude", "longitude"];
  if (required.includes(field) && value === "") {
    return { level: "error", note: "required" };
  }
  if (value === "") return null;

  if (field === "latitude" || field === "longitude") {
    const n = Number(value);
    if (Number.isNaN(n)) return { level: "error", note: "not a number" };
    if (!Number.isFinite(n)) return { level: "error", note: "not finite" };
    const limit = field === "latitude" ? 90 : 180;
    if (Math.abs(n) > limit) {
      return { level: "error", note: `outside ±${limit}` };
    }
    // A latitude beyond ±90 in the longitude column is a hard error above; the
    // reverse — a longitude sitting in latitude — is only ever a suspicion,
    // since a real latitude can be any value in range.
    return null;
  }

  if (field === "stop_sequence") {
    const n = Number(value);
    if (!Number.isInteger(n)) return { level: "error", note: "must be a whole number" };
    if (n < 0) return { level: "error", note: "cannot be negative" };
    return null;
  }

  if (field === "service_time_minutes" || field === "demand_units") {
    const n = Number(value);
    if (Number.isNaN(n)) return { level: "error", note: "not a number" };
    if (n < 0) return { level: "error", note: "cannot be negative" };
    return null;
  }

  if (field === "time_window_start" || field === "time_window_end") {
    if (!/^\d{1,2}:\d{2}(:\d{2})?$/.test(value)) {
      return { level: "warn", note: "expected HH:MM" };
    }
    return null;
  }

  return null;
}

export default function RowPreview({
  headers,
  rows,
  mapping,
}: {
  headers: readonly string[];
  rows: readonly Record<string, string>[];
  mapping: Mapping;
}) {
  const shown = headers.filter((h) => mapping[h] !== null);
  const seqHeader = headers.find((h) => mapping[h] === "stop_sequence");
  const hasDepotRow =
    seqHeader !== undefined && rows.some((row) => (row[seqHeader] ?? "").trim() === "0");

  const issueCount = rows.reduce((count, row) => {
    return (
      count +
      shown.filter((h) => checkCell(mapping[h] ?? null, row[h] ?? "")?.level === "error").length
    );
  }, 0);

  return (
    <div className="preview">
      {issueCount > 0 ? (
        <p className="preview-issues" role="status">
          {issueCount} value{issueCount === 1 ? "" : "s"} in these rows look wrong. You can still
          upload — the server has the final say — but they are likely to be rejected.
        </p>
      ) : null}

      {seqHeader && !hasDepotRow ? (
        <p className="preview-issues" role="status">
          No depot row in the first {rows.length} rows. Each route needs one row with{" "}
          <code>stop_sequence = 0</code> for the depot it starts and ends at. If your depots are
          further down the file, ignore this.
        </p>
      ) : null}

      <div className="preview-scroll">
        <table className="metrics-table preview-table">
          <thead>
            <tr>
              {shown.map((h) => (
                <th key={h} scope="col">
                  <span className="preview-target">{mapping[h] ?? h}</span>
                  {mapping[h] && mapping[h] !== h ? (
                    <span className="preview-source">{h}</span>
                  ) : null}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, i) => (
              <tr key={i}>
                {shown.map((h) => {
                  const raw = row[h] ?? "";
                  const issue = checkCell(mapping[h] ?? null, raw);
                  return (
                    <td key={h} className={issue ? `cell-${issue.level}` : ""}>
                      {raw === "" ? <span className="cell-empty">—</span> : raw}
                      {issue ? <span className="cell-note">{issue.note}</span> : null}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
