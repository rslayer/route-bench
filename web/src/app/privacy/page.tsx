import type { Metadata } from "next";
import Link from "next/link";
import { CONTACT_EMAIL } from "@/components/Footer";

export const metadata: Metadata = {
  title: "Privacy — RouteBench",
  description: "What RouteBench keeps, for how long, and what it never does.",
};

/**
 * The privacy note.
 *
 * Written to be checkable rather than comprehensive: every retention window
 * here corresponds to a real setting, and the deletion it promises is performed
 * by the retention job. Do not add a promise here without the code to keep it —
 * the previous version of this text said uploads were "deleted on a retention
 * schedule" while the deletion routine was a no-op.
 */
export default function PrivacyPage() {
  return (
    <div className="container prose-page">
      <h1>Privacy</h1>
      <p className="lede">
        RouteBench ingests real customer addresses, so the honest version of this page
        matters more than a short one. Here is exactly what happens to your file.
      </p>

      <h2 id="what">What you send</h2>
      <p>
        The CSV you upload — stop coordinates, and whatever optional columns you included,
        which may contain customer identifiers and delivery time windows. That file is
        used to run your analysis and for nothing else.
      </p>

      <h2 id="retention">How long it is kept</h2>
      <table className="metrics-table">
        <thead>
          <tr>
            <th>What</th>
            <th>Kept for</th>
            <th>Then</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td>Your uploaded CSV</td>
            <td>72 hours</td>
            <td>Deleted</td>
          </tr>
          <tr>
            <td>Your report, analysis and map data</td>
            <td>72 hours</td>
            <td>Deleted</td>
          </tr>
          <tr>
            <td>
              Run metadata — timing, token counts, cost. No addresses, no customer data.
            </td>
            <td>30 days</td>
            <td>Deleted</td>
          </tr>
        </tbody>
      </table>
      <p>
        After 72 hours the link stops working and returns an explanation rather than the
        report. Deletion is performed by a scheduled job, not on request.
      </p>

      <h2 id="access">Who can see it</h2>
      <p>
        There are no accounts. Your run is addressed by a random 32-character identifier
        that is not listed, not indexed, and not guessable — anyone with the link can open
        the report, and anyone without it cannot. Treat the link like the report itself.
      </p>

      <h2 id="never">What we never do</h2>
      <ul className="about-list">
        <li>
          <strong>We do not train anything on your data</strong>, and we do not sell,
          share or publish it.
        </li>
        <li>
          <strong>We do not build a dataset from your routes.</strong> Nothing you upload
          is retained beyond the windows above for any purpose, including our own
          research.
        </li>
        <li>
          <strong>We do not track you.</strong> No analytics, no advertising, no
          third-party cookies. The only thing stored in your browser is your theme
          preference and the list of{" "}
          <Link href="/runs">your own run links</Link>, both of which stay on your device.
        </li>
      </ul>

      <h2 id="third-parties">Where it goes</h2>
      <p>
        Your file is processed on RouteBench&rsquo;s own infrastructure. Two outbound
        dependencies exist:
      </p>
      <ul className="about-list">
        <li>
          <strong>Road network distances</strong> are computed by a self-hosted routing
          engine. Coordinates do not leave our infrastructure for this.
        </li>
        <li>
          <strong>Written summaries</strong>, when enabled, are produced by Anthropic&rsquo;s
          API. It receives the computed findings — figures such as distances, times and
          route identifiers — in order to phrase them. Your raw file is not sent. If this
          is not acceptable for your data, the analysis runs identically without it and
          fills the same findings from templates.
        </li>
      </ul>
      <p>
        Map tiles are fetched by your browser from the basemap provider when you view a
        result, which necessarily tells them your IP address, as any map does.
      </p>

      <h2 id="contact">Questions</h2>
      <p>
        Ask, and if something here is wrong or unclear it will be fixed:{" "}
        <a href={`mailto:${CONTACT_EMAIL}`}>{CONTACT_EMAIL}</a>.
      </p>

      <p className="back-home">
        <Link href="/about">About RouteBench →</Link>
      </p>
    </div>
  );
}
