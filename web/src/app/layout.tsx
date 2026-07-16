import type { Metadata } from "next";
import Footer from "@/components/Footer";
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
    <html lang="en">
      <body>
        {/* Keyboard users should not have to tab the whole nav to reach content. */}
        <a href="#main" className="skip-link">
          Skip to content
        </a>
        <div className="page">
          <main id="main">{children}</main>
          <Footer />
        </div>
      </body>
    </html>
  );
}
