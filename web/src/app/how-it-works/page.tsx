import type { Metadata } from "next";
import Link from "next/link";
import Scorecard from "@/components/Scorecard";
import {
  SOLVER_DISCLAIMER,
  SOLVER_ENGINES,
  SUPPORTED_CONSTRAINTS,
  UNSUPPORTED_CONSTRAINTS,
} from "@/lib/constraints";
import { DEPOT_ROW_NOTE, SCHEMA } from "@/lib/schema";
import sampleGrade from "@/lib/sample-grade.json";
import type { Grade } from "@/lib/types";

export const metadata: Metadata = {
  title: "How RouteBench works",
  description:
    "The scoring dimensions, the benchmark approach, what we model and what we do not.",
};

const SAMPLE = sampleGrade.grade as unknown as Grade;
const SAMPLE_FLEET = sampleGrade.fleet;

const DIMENSION_NOTES: Record<string, string> = {
  sequencing:
    "Are the stops on each route in a sensible order? Measured against the solver's own re-ordering of your stops.",
  fleet:
    "Are the right stops on the right routes, and is the work shared evenly? Measured against a cross-route re-solve, plus the spread of workload across your fleet.",
  time: "How much of the day is spent waiting, and do shifts run long?",
  compliance:
    "Are committed time windows kept, and are operational rules like the lunch break respected?",
  density:
    "Is stop density consistent across routes, and do territories overlap? Measured against the rest of your fleet, not an industry average.",
};

export default function HowItWorks() {
  return (
    <div className="container prose-page">
      <h1>How RouteBench works</h1>
      <p className="lede">
        An independent referee for a route plan: it scores what you built, then
        re-solves your own stops to show what was achievable.
      </p>

      {/* ---------------------------------------------------------------- */}
      <h2 id="score">The quality score</h2>
      <p>
        Five dimensions, each scored 0&ndash;100, combined into a weighted mean
        and a letter grade. Every score decomposes to metrics you can see in the
        report &mdash; no model judgement anywhere in the computation, and the
        same fleet always produces the same grade.
      </p>

      <dl className="dimension-notes">
        {SAMPLE.dimensions.map((d) => (
          <div key={d.key}>
            <dt>
              {d.label}
              <span className="dim-weight">
                {" "}
                · {Math.round((SAMPLE.weights[d.key] ?? 0) * 100)}% of the score
              </span>
            </dt>
            <dd>{DIMENSION_NOTES[d.key]}</dd>
          </div>
        ))}
      </dl>

      <p>
        Where a dimension cannot be computed for your fleet &mdash; a single
        route has no balance to assess, a fleet with no time windows has no
        violation rate &mdash; it is marked <em>not graded</em> and its weight is
        spread across the rest. It never counts as a zero.
      </p>

      <p className="callout">
        The rubric is <strong>engineering judgment, not a peer percentile</strong>.
        RouteBench has no cross-customer data and does not compare you to other
        companies or to an industry average. The thresholds encode what we
        consider good and poor practice, and every report records the rubric
        version it was graded under (currently v{SAMPLE.grading_version}) so a
        future change can never silently reinterpret an old score.
      </p>

      <h3 id="sample">Example: our sample fleet</h3>
      <p>
        A published fleet of {SAMPLE_FLEET.routes} routes and {SAMPLE_FLEET.stops}{" "}
        stops, hand-built to contain a problem in every category &mdash; which is
        why it grades poorly. It is a demonstration of the shape of a result, not
        a target.
      </p>
      <Scorecard grade={SAMPLE} caption="Sample fleet" />

      {/* ---------------------------------------------------------------- */}
      <h2 id="solvers">The benchmark</h2>
      <p>
        Scoring your plan against a threshold only goes so far &mdash; the useful
        question is what was actually achievable with <em>your</em> stops, under{" "}
        <em>your</em> constraints. So RouteBench re-solves the problem itself and
        compares.
      </p>

      <ul className="engines">
        {SOLVER_ENGINES.map((engine) => (
          <li key={engine.name}>
            <h3>{engine.name}</h3>
            <p className="engine-scope">{engine.scope}</p>
            <p>{engine.description}</p>
          </li>
        ))}
      </ul>

      <p>
        Both run on{" "}
        <a
          href="https://developers.google.com/optimization"
          target="_blank"
          rel="noopener noreferrer"
        >
          Google OR-Tools
        </a>{" "}
        under guided local search with a fixed time budget. Travel times and
        distances come from OpenStreetMap road data via{" "}
        <a href="https://project-osrm.org/" target="_blank" rel="noopener noreferrer">
          OSRM
        </a>
        , optionally scaled by a traffic profile you choose.
      </p>

      <p className="disclaimer" role="note">
        {SOLVER_DISCLAIMER}
      </p>

      <p>
        The practical consequence: a reported saving is a <strong>floor</strong>,
        not a ceiling. A better solution may exist that the solver did not find in
        its budget. And where the solver finds nothing better than your plan, we
        say so plainly &mdash; that is a good result, not a zero.
      </p>

      {/* ---------------------------------------------------------------- */}
      <h2 id="constraints">What we model</h2>
      <p>
        Every setting below is yours to change before you upload, and whatever you
        choose is exactly what runs &mdash; there are no hidden defaults that
        differ from what you saw. Turning a constraint off removes it from both
        the scoring and the solvers, and the report&rsquo;s methodology page
        records which were active.
      </p>

      <table className="metrics-table">
        <thead>
          <tr>
            <th scope="col">Constraint</th>
            <th scope="col">What it does</th>
          </tr>
        </thead>
        <tbody>
          {SUPPORTED_CONSTRAINTS.map((c) => (
            <tr key={c.id}>
              <th scope="row">{c.label}</th>
              <td>
                {c.description}
                {c.requiresColumns ? (
                  <span className="requires">
                    {" "}
                    Needs: {c.requiresColumns.join(", ")}.
                  </span>
                ) : null}
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      <h2 id="not-supported">What we do not model</h2>
      <p>
        A benchmarking tool that hides its own limits is not a referee. If your
        operation depends on one of these, read the grade with that in mind.
      </p>

      <table className="metrics-table">
        <thead>
          <tr>
            <th scope="col">Not yet supported</th>
            <th scope="col">What that means</th>
            <th scope="col">Status</th>
          </tr>
        </thead>
        <tbody>
          {UNSUPPORTED_CONSTRAINTS.map((c) => (
            <tr key={c.label}>
              <th scope="row">{c.label}</th>
              <td>{c.note}</td>
              <td>
                <span className={`tag tag-${c.tag === "Planned" ? "planned" : "considering"}`}>
                  {c.tag}
                </span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      {/* ---------------------------------------------------------------- */}
      <h2 id="format">The file</h2>
      <p>
        A CSV, one row per stop. Only four columns are required; the rest sharpen
        the analysis where you have them. Your own column names are fine &mdash;
        after you drop the file we show you the mapping we inferred and let you
        correct it.
      </p>
      <p className="callout">{DEPOT_ROW_NOTE}</p>

      <table className="metrics-table">
        <thead>
          <tr>
            <th scope="col">Column</th>
            <th scope="col">Required</th>
            <th scope="col">Description</th>
            <th scope="col">Example</th>
          </tr>
        </thead>
        <tbody>
          {SCHEMA.map((field) => (
            <tr key={field.name}>
              <th scope="row">
                <code>{field.name}</code>
              </th>
              <td>{field.required ? "Yes" : "—"}</td>
              <td>{field.description}</td>
              <td>
                <code>{field.example}</code>
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      <p>
        <a href="/api/template" download="routebench-template.csv">
          Download the template CSV
        </a>
      </p>

      {/* ---------------------------------------------------------------- */}
      <h2 id="faq">Questions</h2>
      <dl className="faq">
        <div>
          <dt>How long does it take?</dt>
          <dd>
            Minutes for a typical fleet. The solvers run under a fixed time
            budget, so a larger fleet takes longer but never runs away.
          </dd>
        </div>
        <div>
          <dt>Do you keep my data?</dt>
          <dd>
            Your upload, report and map data are deleted 72 hours after the run.
            Run metadata with no customer data in it — timing and cost — is kept
            for 30 days. There are no accounts, your session is reachable only
            through its unguessable link, and nothing you upload is used to train
            anything or kept as a dataset. Full detail in the{" "}
            <Link href="/privacy">privacy note</Link>.
          </dd>
        </div>
        <div>
          <dt>Is this a routing optimiser?</dt>
          <dd>
            No. RouteBench grades a plan and shows what a solver achieved on the
            same stops. It does not produce routes for your drivers to run, and it
            does not model everything a real dispatcher must &mdash; see{" "}
            <Link href="#not-supported">what we do not model</Link>.
          </dd>
        </div>
        <div>
          <dt>Why is my grade lower than I expected?</dt>
          <dd>
            The score is a rubric, not a curve &mdash; nothing is graded relative
            to other customers. Open the dimension that scored lowest: every score
            decomposes to specific findings with the routes and stops behind them.
          </dd>
        </div>
        <div>
          <dt>Can I see how the score is calculated?</dt>
          <dd>
            Yes. The full rubric &mdash; weights, thresholds, and letter bands
            &mdash; is published in every report&rsquo;s methodology section, and
            the source is on{" "}
            <a
              href="https://github.com/rslayer/route-bench"
              target="_blank"
              rel="noopener noreferrer"
            >
              GitHub
            </a>
            .
          </dd>
        </div>
      </dl>

      <p className="back-home">
        <Link href="/">← Score your own routes</Link>
      </p>
    </div>
  );
}
