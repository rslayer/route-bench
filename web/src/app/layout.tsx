import type { Metadata } from "next";
import Footer from "@/components/Footer";
import ThemeToggle from "@/components/ThemeToggle";
import { THEME_INIT_SCRIPT } from "@/lib/theme";
import "./globals.css";

export const metadata: Metadata = {
  title: "RouteBench — independent last-mile route benchmarking",
  description:
    "Upload a route plan, get an independent quality score in minutes.",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    // suppressHydrationWarning: the script below stamps data-theme before React
    // sees the DOM, so the server's <html> and the client's differ by design.
    <html lang="en" suppressHydrationWarning>
      <head>
        {/* Blocking and inline, before any paint. Anything later means a white
            flash for every dark-mode user on every navigation. */}
        <script dangerouslySetInnerHTML={{ __html: THEME_INIT_SCRIPT }} />
      </head>
      <body>
        {/* Keyboard users should not have to tab the whole nav to reach content. */}
        <a href="#main" className="skip-link">
          Skip to content
        </a>
        <div className="page">
          <div className="topbar">
            <ThemeToggle />
          </div>
          <main id="main">{children}</main>
          <Footer />
        </div>
      </body>
    </html>
  );
}
