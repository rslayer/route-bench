import type { Metadata } from "next";
import Link from "next/link";
import { CONTACT_EMAIL, REPO_URL } from "@/components/Footer";

export const metadata: Metadata = {
  title: "About — RouteBench",
  description:
    "What RouteBench is, what it refuses to do, and why an independent benchmark is worth having.",
};

export default function AboutPage() {
  return (
    <div className="container prose-page">
      <h1>About RouteBench</h1>
      <p className="lede">
        RouteBench grades a delivery route plan you already have, and shows what an
        independent solver achieved on the same stops under the same constraints.
      </p>

      <h2 id="why">Why it exists</h2>
      <p>
        Almost every routing tool grades its own homework. The software that builds your
        routes is usually also the software that reports how good they are, and it has no
        incentive to tell you it left ten percent on the table. If you want a second
        opinion, you generally have to buy a second routing system.
      </p>
      <p>
        RouteBench is that second opinion, and nothing else. It does not build your
        routes, it does not want to replace your planner, and it has no stake in the
        answer. It takes the plan you already run, re-solves the same stops with an
        open-source solver, and reports the difference.
      </p>

      <h2 id="honesty">What it refuses to do</h2>
      <p>
        The whole value of a benchmark is that you can trust the number, so the design
        rule throughout is that RouteBench would rather tell you nothing than tell you
        something it cannot support.
      </p>
      <ul className="about-list">
        <li>
          <strong>It withholds the score rather than estimating it.</strong> When the road
          network service is unavailable, travel times fall back to straight-line
          estimates. The routes, stops and findings still hold, and every number is
          labelled — but the letter grade is withheld, because a grade computed from
          estimated distances would look more precise than it is.
        </li>
        <li>
          <strong>The language model never invents a number.</strong> Findings are
          computed deterministically. A model rewrites them into readable prose, then a
          verifier checks every figure in that prose against the source data and rejects
          it if anything does not match. If there is no model configured, the same
          findings are filled from templates and the analysis is otherwise identical.
        </li>
        <li>
          <strong>The solver&rsquo;s result is a floor, not a ceiling.</strong> It runs
          under a fixed time budget and does not model everything your dispatchers know.
          A gap it finds is real; the absence of a gap is not proof there is none.
        </li>
        <li>
          <strong>It says what it does not model.</strong> The{" "}
          <Link href="/how-it-works#not-supported">methodology page</Link> lists the
          constraints RouteBench ignores, because a benchmark that quietly drops your
          hardest constraint is worse than no benchmark.
        </li>
      </ul>

      <h2 id="independence">Independence</h2>
      <p>
        RouteBench is not affiliated with any routing vendor, is not funded by one, and
        does not resell one. The scoring rules, the solver settings and the full source
        are{" "}
        <a href={REPO_URL} target="_blank" rel="noopener noreferrer">
          published
        </a>
        , so you can check that the grade you were given is the grade the rules produce.
      </p>

      <h2 id="data">Your data</h2>
      <p>
        There are no accounts. A run is reachable only through its unguessable link, your
        upload and report are deleted on a schedule, and nothing you upload is used to
        train anything. The <Link href="/privacy">privacy note</Link> gives the specifics,
        including the retention windows.
      </p>

      <h2 id="who">Who made it</h2>
      <p>
        Built by Ali Kamil. Questions, disagreements about the methodology, and reports of
        it getting something wrong are all welcome at{" "}
        <a href={`mailto:${CONTACT_EMAIL}`}>{CONTACT_EMAIL}</a> or as an issue on{" "}
        <a href={REPO_URL} target="_blank" rel="noopener noreferrer">
          the repository
        </a>
        .
      </p>

      <p className="back-home">
        <Link href="/guide">How to use it →</Link>
      </p>
    </div>
  );
}
