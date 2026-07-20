/**
 * Theme: the user's choice, defaulting to light.
 *
 * Three states. Light is the default — a fresh visitor gets a white background
 * regardless of what their OS prefers, because this is a document-style tool
 * that reads best on light and we want a predictable first impression rather
 * than one that changes with the reader's OS. "System" is still offered for
 * anyone who would rather follow the OS, and Dark is the explicit override.
 *
 * The resolved theme is written to <html data-theme> so CSS can key on it
 * directly, and the choice is persisted so it survives a reload.
 */

export type ThemeChoice = "system" | "light" | "dark";
export type ResolvedTheme = "light" | "dark";

export const THEME_KEY = "routebench-theme";

export const DEFAULT_CHOICE: ThemeChoice = "light";

export function resolveTheme(choice: ThemeChoice): ResolvedTheme {
  if (choice !== "system") return choice;
  if (typeof window === "undefined") return "light";
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

export function readChoice(): ThemeChoice {
  if (typeof window === "undefined") return DEFAULT_CHOICE;
  const stored = window.localStorage.getItem(THEME_KEY);
  // Only an explicit stored value overrides the default. An unset value — a
  // first visit — resolves to light, not to the OS preference.
  return stored === "light" || stored === "dark" || stored === "system"
    ? stored
    : DEFAULT_CHOICE;
}

export function applyTheme(choice: ThemeChoice): ResolvedTheme {
  const resolved = resolveTheme(choice);
  document.documentElement.dataset.theme = resolved;
  return resolved;
}

/**
 * Runs before first paint, inlined into <head>.
 *
 * Without this the page renders at the CSS default and then corrects itself once
 * React hydrates — a white flash for every dark-mode user on every navigation.
 * It has to be a blocking inline script; there is no way to be both
 * server-rendered and know the client's preference.
 */
export const THEME_INIT_SCRIPT = `
(function () {
  try {
    var stored = localStorage.getItem('${THEME_KEY}');
    // Dark only when explicitly chosen, or when the reader picked "system" and
    // their OS is dark. An unset value is a first visit and resolves to light,
    // matching DEFAULT_CHOICE — the OS preference no longer decides the default.
    var dark = stored === 'dark' ||
      (stored === 'system' && window.matchMedia('(prefers-color-scheme: dark)').matches);
    document.documentElement.dataset.theme = dark ? 'dark' : 'light';
  } catch (e) {
    // Private mode can throw on localStorage; fall back to the light default.
    document.documentElement.dataset.theme = 'light';
  }
})();
`.trim();
