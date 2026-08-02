import coreWebVitals from "eslint-config-next/core-web-vitals";
import typescript from "eslint-config-next/typescript";

// eslint-config-next 16 ships native flat configs (arrays), so they are spread
// directly. The FlatCompat bridge was only needed while the config was still in
// the legacy eslintrc format.
const config = [
  ...coreWebVitals,
  ...typescript,
  { ignores: [".next/**", "node_modules/**", "playwright-report/**", "test-results/**"] },
];

export default config;
