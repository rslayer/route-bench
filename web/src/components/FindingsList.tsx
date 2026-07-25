"use client";

import { SEVERITY_COLORS, SEVERITY_TEXT } from "@/lib/palette";
import { SEVERITY_ORDER, type Finding } from "@/lib/types";

/**
 * The findings, and the other half of the map linkage.
 *
 * Selecting a finding highlights its routes on the map; selecting a route on
 * the map filters this list. That loop is the point — it is what turns a
 * deterministic finding into something you can see on the ground.
 */

const CATEGORY_LABEL: Record<string, string> = {
  sequencing: "Sequencing",
  time_pressure: "Time pressure",
  utilization: "Utilization",
  compliance: "Compliance",
  territory: "Territory",
  dispatch: "Dispatch",
  outlier: "Outlier",
  reachability: "Reachability",
};

function Evidence({ finding }: { finding: Finding }) {
  return (
    <dl className="evidence">
      {finding.evidence.map((e) => (
        <div key={e.metric_name}>
          <dt>{e.metric_name.replace(/_/g, " ")}</dt>
          <dd>
            {e.actual_value.toFixed(2)} {e.unit}
            {e.comparison_value != null ? (
              <span className="evidence-vs">
                {" vs "}
                {e.comparison_value.toFixed(2)} {e.comparison_type ?? ""}
              </span>
            ) : null}
          </dd>
        </div>
      ))}
    </dl>
  );
}

export default function FindingsList({
  findings,
  selectedFindingId,
  onSelectFinding,
  filterRouteId,
  onClearFilter,
}: {
  findings: Finding[];
  selectedFindingId: string | null;
  onSelectFinding: (id: string | null) => void;
  filterRouteId: string | null;
  onClearFilter: () => void;
}) {
  const shown = filterRouteId
    ? findings.filter((f) => f.references.route_ids.includes(filterRouteId))
    : findings;

  const sorted = [...shown].sort(
    (a, b) => SEVERITY_ORDER[a.severity] - SEVERITY_ORDER[b.severity],
  );

  return (
    <div className="findings">
      <div className="findings-head">
        <h2 className="section-title">
          Findings <span className="findings-count">{sorted.length}</span>
        </h2>
        {filterRouteId ? (
          <button type="button" className="btn-link" onClick={onClearFilter}>
            Showing {filterRouteId} only — show all
          </button>
        ) : null}
      </div>

      {sorted.length === 0 ? (
        <p className="dim-note">
          {filterRouteId
            ? `No findings reference ${filterRouteId}.`
            : "No findings — nothing crossed a threshold on this fleet."}
        </p>
      ) : (
        <ul className="finding-list">
          {sorted.map((finding) => {
            const selected = finding.finding_id === selectedFindingId;
            return (
              <li key={finding.finding_id}>
                <button
                  type="button"
                  className={`finding${selected ? " is-selected" : ""}`}
                  aria-expanded={selected}
                  onClick={() =>
                    onSelectFinding(selected ? null : finding.finding_id)
                  }
                >
                  <span className="finding-top">
                    <span
                      className="sev"
                      style={{
                        background: SEVERITY_COLORS[finding.severity],
                        color: SEVERITY_TEXT[finding.severity],
                      }}
                    >
                      {finding.severity}
                    </span>
                    <span className="finding-cat">
                      {CATEGORY_LABEL[finding.category] ?? finding.category}
                    </span>
                    {finding.references.route_ids.length > 0 ? (
                      <span className="finding-routes">
                        {finding.references.route_ids.slice(0, 3).join(", ")}
                        {finding.references.route_ids.length > 3
                          ? ` +${finding.references.route_ids.length - 3}`
                          : ""}
                      </span>
                    ) : null}
                  </span>
                  <span className="finding-title">{finding.title}</span>
                </button>

                {selected ? (
                  <div className="finding-body">
                    <p>{finding.hypothesis}</p>
                    <Evidence finding={finding} />
                    <p className="finding-next">
                      <strong>Suggested next step:</strong> {finding.suggested_investigation}
                    </p>
                    <p className="dim-note">
                      Confidence {(finding.confidence * 100).toFixed(0)}% · id{" "}
                      <code>{finding.finding_id}</code>
                    </p>
                  </div>
                ) : null}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
