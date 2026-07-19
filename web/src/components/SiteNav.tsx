"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import ThemeToggle from "@/components/ThemeToggle";

/**
 * Site navigation.
 *
 * There was none — every page but the landing page was reachable only by
 * guessing a URL or following a link that happened to be in body copy, which
 * made the guide and the methodology page effectively invisible.
 *
 * Deliberately short. Five destinations is the whole site, and a nav that lists
 * everything is a nav nobody reads.
 */

// "Analyse a plan" points at "/", not "/upload": the upload flow starts with
// the dropzone on the landing page, and /upload without a file staged from it
// is a dead end that reads as "you got kicked back to the start". Starting a
// new analysis and resuming an in-progress one are different needs — the first
// is this link, the second is the ResumeBanner.
const LINKS: { href: string; label: string }[] = [
  { href: "/", label: "Analyse a plan" },
  { href: "/guide", label: "How to use it" },
  { href: "/how-it-works", label: "Methodology" },
  { href: "/about", label: "About" },
  { href: "/runs", label: "Your runs" },
];

export default function SiteNav() {
  const pathname = usePathname();

  return (
    <header className="topbar">
      <Link href="/" className="brand">
        RouteBench
      </Link>

      <nav className="site-nav" aria-label="Main">
        {LINKS.map(({ href, label }) => {
          // Exact match only. Prefix matching would light up "Analyse a plan"
          // on every /upload/* child and, worse, mark nothing current on /s/[id],
          // which is where users spend the most time.
          const current = pathname === href;
          return (
            <Link
              key={href}
              href={href}
              className={current ? "is-current" : ""}
              aria-current={current ? "page" : undefined}
            >
              {label}
            </Link>
          );
        })}
      </nav>

      <ThemeToggle />
    </header>
  );
}
