import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  // Standalone traces only the deps actually imported, so the Fly image ships
  // ~100MB instead of the whole node_modules tree.
  output: "standalone",
  // The API base and build identity are read at build time so the footer and
  // the client both know what they are talking to. See src/lib/config.ts.
  env: {
    NEXT_PUBLIC_API_BASE: process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000",
  },
};

export default nextConfig;
