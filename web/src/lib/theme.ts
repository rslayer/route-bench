/**
 * Theme: the user's choice, with the OS as the default.
 *
 * Three states, not two. "System" is the default and means "follow the OS",
 * which is what most people want and what the CSS already did. Light and Dark
 * are explicit overrides for the case where someone's OS says one thing and
 * they want the other — a dark-mode user reading a report in daylight, say.
 *
 * The resolved theme is written to <html data-theme> so CSS can key on it
 * directly, and the choice is persisted so it survives a reload.
 */

export type ThemeChoice = "system" | "light" | "dark";
export type ResolvedTheme = "light" | "dark";

export const THEME_KEY = "routebench-theme";

export function resolveTheme(choice: ThemeChoice): ResolvedTheme {
  if (choice !== "system") return choice;
  if (typeof window === "undefined") return "light";
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

export function readChoice(): ThemeChoice {
  if (typeof window === "undefined") return "system";
  const stored = window.localStorage.getItem(THEME_KEY);
  return stored === "light" || stored === "dark" ? stored : "system";
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
    var dark = stored === 'dark' ||
      ((!stored || stored === 'system') &&
       window.matchMedia('(prefers-color-scheme: dark)').matches);
    document.documentElement.dataset.theme = dark ? 'dark' : 'light';
  } catch (e) {
    // Private mode can throw on localStorage. The CSS media query still
    // applies, so the page is themed correctly — just not overridable.
  }
})();
`.trim();
