"use client";

import { useEffect, useState } from "react";
import {
  THEME_KEY,
  applyTheme,
  readChoice,
  type ThemeChoice,
} from "@/lib/theme";

/**
 * Light / dark / follow-the-OS.
 *
 * A radio group rather than a two-state switch: with a plain toggle there is no
 * way to say "follow my OS", which is what most people want and what someone who
 * switches at sunset needs. Three real states, one of them the default.
 */

const OPTIONS: { value: ThemeChoice; label: string; icon: string }[] = [
  // Simple glyphs rather than an icon dependency — three characters do not
  // justify a package.
  { value: "light", label: "Light", icon: "☀" },
  { value: "system", label: "System", icon: "◐" },
  { value: "dark", label: "Dark", icon: "☾" },
];

export default function ThemeToggle() {
  // Starts as null so the button labels are not rendered until we know the real
  // choice; rendering "system" first and correcting it would flash the wrong
  // selection on every load.
  const [choice, setChoice] = useState<ThemeChoice | null>(null);

  useEffect(() => {
    setChoice(readChoice());
  }, []);

  // When following the OS, keep following it: someone whose machine switches at
  // sunset expects the page to switch with it, not at their next reload.
  useEffect(() => {
    if (choice !== "system") return;
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    const onChange = () => applyTheme("system");
    media.addEventListener("change", onChange);
    return () => media.removeEventListener("change", onChange);
  }, [choice]);

  const pick = (next: ThemeChoice) => {
    setChoice(next);
    applyTheme(next);
    try {
      // Store all three choices explicitly, "system" included. This used to
      // remove the key for "system" on the logic that an absent key WAS system
      // — but the default is now light, so an absent key reads as light. Storing
      // "system" is what makes the choice survive a reload; removing it would
      // silently revert the user to light next time they load the page.
      window.localStorage.setItem(THEME_KEY, next);
    } catch {
      // Private mode. The theme still applies for this page view; it just will
      // not be remembered.
    }
  };

  return (
    <div className="theme-toggle" role="radiogroup" aria-label="Colour theme">
      {OPTIONS.map((option) => (
        <button
          key={option.value}
          type="button"
          role="radio"
          aria-checked={choice === option.value}
          className={choice === option.value ? "is-active" : ""}
          onClick={() => pick(option.value)}
          title={`${option.label} theme`}
        >
          <span aria-hidden="true">{option.icon}</span>
          <span className="sr-only">{option.label}</span>
        </button>
      ))}
    </div>
  );
}
