import type { Metadata } from "next";
import Link from "next/link";

export const metadata: Metadata = {
  title: "How to use RouteBench",
  description:
    "Step by step: prepare a file, run an analysis, and read the result.",
};

/**
 * The practical guide.
 *
 * Deliberately separate from /how-it-works, which explains the scoring model.
 * That page answers "should I believe this?"; this one answers "what do I do?".
 * They were previously one page, which meant a first-time user had to read the
 * grading weights before finding out which columns to include.
 */
export default function GuidePage() {
  return (
    <div className="container prose-page">
      <h1>How to use RouteBench</h1>
      <p className="lede">
        Five minutes end to end. You need a CSV of a route plan you have already run or
        are about to run — one row per stop.
      </p>

      <h2 id="step-1">1. Get your file ready</h2>
      <p>
        Export a day of routes from whatever you plan in. Most systems can produce this
        directly; a spreadsheet works too. One row per stop, with at least:
      </p>
      <table className="metrics-table">
        <thead>
          <tr>
            <th>Column</th>
            <th>What it is</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td>
              <code>route_id</code>
            </td>
            <td>Which vehicle or driver the stop belongs to. Any label.</td>
          </tr>
          <tr>
            <td>
              <code>stop_sequence</code>
            </td>
            <td>The order the driver was told to visit them in, starting at 1.</td>
          </tr>
          <tr>
            <td>
              <code>latitude</code>, <code>longitude</code>
            </td>
            <td>Decimal degrees. This is what the analysis actually measures.</td>
          </tr>
        </tbody>
      </table>
      <p>
        Everything else is optional and makes the result sharper: service time, delivery
        time windows, planned arrival times, demand, and a depot row.{" "}
        <Link href="/how-it-works#format">The full column reference</Link> lists them,
        and the uploader will offer a template.
      </p>
      <p className="callout">
        Names do not have to match exactly. The uploader shows you every column it found
        and lets you map them, so <code>Stop #</code> or <code>DriverID</code> is fine.
      </p>

      <h2 id="step-2">2. Upload it</h2>
      <p>
        Drop the file on <Link href="/upload">the upload page</Link>. Nothing is sent
        anywhere until you confirm: the file is parsed in your browser first, so you can
        check the column mapping and see a preview of your routes before committing.
      </p>

      <h2 id="step-3">3. Set your constraints</h2>
      <p>
        The defaults are reasonable, but the benchmark is only fair if the solver plays by
        your rules. If your drivers work a nine-hour day, or you require a lunch break, or
        your vehicles have a capacity — say so here. A solver allowed to ignore a
        constraint you actually have will find savings that do not exist, and the
        comparison becomes worthless.
      </p>

      <h2 id="step-4">4. Wait</h2>
      <p>
        A few minutes for a typical fleet. The solvers run under a fixed time budget, so
        the page can tell you how long is left rather than guessing — a bigger fleet takes
        longer but never runs away.
      </p>
      <p className="callout">
        You can close the tab. The analysis keeps running, and the link stays good — it is
        also saved under <Link href="/runs">Your runs</Link> on this browser.
      </p>

      <h2 id="step-5">5. Read the result</h2>
      <dl className="faq">
        <div>
          <dt>The score</dt>
          <dd>
            A letter grade across several dimensions. Start with the weakest one — that is
            where the recoverable time is.{" "}
            <Link href="/how-it-works#score">How the score is built</Link>.
          </dd>
        </div>
        <div>
          <dt>The headline gap</dt>
          <dd>
            How much shorter the solver&rsquo;s fleet plan was than yours, under your
            constraints. Treat it as a floor: the solver stops at its time budget and does
            not know everything your dispatchers do.
          </dd>
        </div>
        <div>
          <dt>The map</dt>
          <dd>
            Switch to <em>Both</em> to see your order and the solver&rsquo;s side by side.
            Where the two drive the same road they are drawn as parallel lines — solid is
            yours, dashed is the solver&rsquo;s. Clicking a finding zooms to the routes it
            affects.
          </dd>
        </div>
        <div>
          <dt>The findings</dt>
          <dd>
            Specific, located problems — a stop out of order, a route that cannot make its
            windows, an imbalanced day. Each one names the routes involved so you can go
            look.
          </dd>
        </div>
      </dl>

      <h2 id="trouble">If something looks wrong</h2>
      <dl className="faq">
        <div>
          <dt>&ldquo;Quality score withheld&rdquo;</dt>
          <dd>
            The road-network service was unreachable, so distances are straight-line
            estimates. Everything else still stands, but the grade is deliberately not
            shown rather than computed from estimates. Re-running later will produce a
            graded result.
          </dd>
        </div>
        <div>
          <dt>The solver found a huge gap</dt>
          <dd>
            Check your constraints first. A missing capacity or shift limit is the usual
            cause — the solver was allowed to do something your operation cannot.
          </dd>
        </div>
        <div>
          <dt>The solver found nothing</dt>
          <dd>
            A real and good result: your plan is already within solver reach on the things
            RouteBench measures. It is not a claim that no improvement exists.
          </dd>
        </div>
        <div>
          <dt>My link stopped working</dt>
          <dd>
            Runs expire after 72 hours and the data is deleted. Re-upload to get a fresh
            analysis.
          </dd>
        </div>
      </dl>

      <p className="back-home">
        <Link href="/upload">Analyse a plan →</Link>
      </p>
    </div>
  );
}
