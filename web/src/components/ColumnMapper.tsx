"use client";

import { useMemo } from "react";
import {
  REQUIRED_FIELDS,
  SCHEMA,
  suggestField,
  type SchemaField,
} from "@/lib/schema";

/**
 * Map a file's columns onto RouteBench fields.
 *
 * Shown only when the headers do not already match — a user with a standard file
 * should never see this screen.
 *
 * The suggestions are deterministic and local (see schema.ts): exact and alias
 * matches only, no LLM, no network. A header we cannot place is left unmapped
 * rather than guessed at, because a wrong auto-mapping the user accepts without
 * reading is worse than no suggestion.
 */

export type Mapping = Record<string, string | null>;

export function suggestMapping(headers: readonly string[]): Mapping {
  const mapping: Mapping = {};
  const taken = new Set<string>();
  for (const header of headers) {
    const suggestion = suggestFieldOnce(header, taken);
    mapping[header] = suggestion;
    if (suggestion) taken.add(suggestion);
  }
  return mapping;
}

/** First header to claim a field wins; a field cannot be mapped twice. */
function suggestFieldOnce(header: string, taken: Set<string>): string | null {
  const suggestion = suggestField(header);
  return suggestion && !taken.has(suggestion) ? suggestion : null;
}

function fieldByName(name: string | null): SchemaField | undefined {
  return name ? SCHEMA.find((f) => f.name === name) : undefined;
}

export function missingRequired(mapping: Mapping): string[] {
  const mapped = new Set(Object.values(mapping).filter(Boolean) as string[]);
  return REQUIRED_FIELDS.filter((name) => !mapped.has(name));
}

export default function ColumnMapper({
  headers,
  mapping,
  onChange,
}: {
  headers: readonly string[];
  mapping: Mapping;
  onChange: (mapping: Mapping) => void;
}) {
  const missing = useMemo(() => missingRequired(mapping), [mapping]);
  const used = useMemo(
    () => new Set(Object.values(mapping).filter(Boolean) as string[]),
    [mapping],
  );

  const set = (header: string, value: string) => {
    onChange({ ...mapping, [header]: value === "" ? null : value });
  };

  return (
    <div className="mapper">
      {missing.length > 0 ? (
        <p className="mapper-missing" role="alert">
          Still needed: {missing.map((m) => <code key={m}>{m}</code>).reduce(
            (acc, el, i) => (i === 0 ? [el] : [...acc, ", ", el]),
            [] as React.ReactNode[],
          )}
          . Pick the column that holds each.
        </p>
      ) : (
        <p className="mapper-ok">All required fields are mapped.</p>
      )}

      <table className="metrics-table mapper-table">
        <thead>
          <tr>
            <th scope="col">Your column</th>
            <th scope="col">RouteBench field</th>
            <th scope="col">What it means</th>
          </tr>
        </thead>
        <tbody>
          {headers.map((header) => {
            const target = mapping[header] ?? null;
            const field = fieldByName(target);
            return (
              <tr key={header} className={target ? "" : "is-unmapped"}>
                <th scope="row">
                  <code>{header}</code>
                </th>
                <td>
                  <label className="sr-only" htmlFor={`map-${header}`}>
                    RouteBench field for {header}
                  </label>
                  <select
                    id={`map-${header}`}
                    value={target ?? ""}
                    onChange={(e) => set(header, e.target.value)}
                  >
                    <option value="">— ignore this column —</option>
                    {SCHEMA.map((f) => (
                      <option
                        key={f.name}
                        value={f.name}
                        // A field already claimed by another column stays
                        // selectable here but is marked, so switching is
                        // possible without first clearing the other row.
                        disabled={f.name !== target && used.has(f.name)}
                      >
                        {f.name}
                        {f.required ? " (required)" : ""}
                        {f.name !== target && used.has(f.name) ? " — already mapped" : ""}
                      </option>
                    ))}
                  </select>
                </td>
                <td className="mapper-desc">
                  {field ? field.description : <span className="dim-note">Not used.</span>}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
