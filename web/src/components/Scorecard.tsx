/**
 * The quality score: letter grade plus dimension bars.
 *
 * Shared by the landing page preview and the results page, so the payoff a
 * visitor is shown before uploading is literally the same component that renders
 * their own result.
 *
 * Two things this must never do:
 *   - render a not_graded dimension as 0 (it means "could not be computed for
 *     this fleet", not "scored badly")
 *   - let colour carry the score alone — every bar prints its number, and the
 *     bar has an aria-label
 */

import type { DimensionGrade, Grade, GradeLetter } from "@/lib/types";

/** ASCII in the data, typographic minus on screen. */
function displayLetter(letter: GradeLetter | null): string {
  return letter ? letter.replace("-", "−") : "—";
}

/** Bands are 60+; below that is F. Hue tracks the band, never alone. */
function letterTone(letter: GradeLetter | null): string {
  if (!letter) return "none";
  const head = letter.charAt(0);
  return ["A", "B", "C", "D"].includes(head) ? head : "F";
}

/** Plain-English gloss of what a score was anchored to. */
const BASIS_NOTE: Record<string, string> = {
  benchmark: "measured against the solver's own solution for this fleet",
  heuristic: "estimated without a solver run — a weaker proxy",
  balance_only: "workload balance only; the cross-route solver did not run",
  absolute: "measured against fixed operational standards",
  operational_only: "operational checks only; this fleet has no time windows",
  fleet_relative: "measured against the rest of this fleet",
  insufficient_routes: "needs at least two routes",
  insufficient_data: "not enough data in the upload",
};

function DimensionRow({
  dimension,
  weight,
}: {
  dimension: DimensionGrade;
  weight: number | undefined;
}) {
  const note = BASIS_NOTE[dimension.basis] ?? dimension.basis.replace(/_/g, " ");

  return (
    <tr>
      <th scope="row">
        {dimension.label}
        <span className="dim-weight">{weight ? ` · ${Math.round(weight * 100)}%` : ""}</span>
      </th>
      <td>
        {dimension.not_graded || dimension.score === null ? (
          <span className="dim-ungraded">
            Not graded
            <span className="dim-note"> — {note}</span>
          </span>
        ) : (
          <>
            <span
              className="bar-track"
              role="img"
              aria-label={`${dimension.score.toFixed(0)} out of 100`}
            >
              <span
                className={`bar-fill tone-${letterTone(dimension.letter)}`}
                style={{ width: `${Math.max(2, dimension.score)}%` }}
              />
            </span>
            <span className="bar-number">
              {dimension.score.toFixed(1)}
              <span className="bar-letter">{displayLetter(dimension.letter)}</span>
            </span>
            <span className="dim-note">{note}</span>
          </>
        )}
      </td>
    </tr>
  );
}

export default function Scorecard({
  grade,
  caption,
}: {
  grade: Grade;
  caption?: string;
}) {
  const { overall, dimensions, weights, grading_version } = grade;
  const ungraded = dimensions.filter((d) => d.not_graded);

  return (
    <div className="scorecard">
      <div className="scorecard-head">
        <span className={`score-letter tone-${letterTone(overall.letter)}`}>
          {displayLetter(overall.letter)}
        </span>
        <span className="score-number">
          {overall.score === null ? "—" : overall.score.toFixed(1)}
          <span className="score-outof">/100</span>
        </span>
        {caption ? <span className="score-caption">{caption}</span> : null}
      </div>

      <table className="dimensions">
        <caption className="sr-only">
          Quality score by dimension, graded under rubric v{grading_version}
        </caption>
        <tbody>
          {dimensions.map((dimension) => (
            <DimensionRow
              key={dimension.key}
              dimension={dimension}
              weight={weights[dimension.key]}
            />
          ))}
        </tbody>
      </table>

      {ungraded.length > 0 ? (
        <p className="scorecard-foot">
          {ungraded.length} dimension{ungraded.length === 1 ? "" : "s"} could not be
          graded for this fleet. The overall score is a weighted mean over the rest,
          with weights renormalised — an ungraded dimension never counts as zero.
        </p>
      ) : null}
    </div>
  );
}
