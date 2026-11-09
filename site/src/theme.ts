/**
 * The site's two themes, after lhzn-blue: Night ("chart room", the default) and Day ("gilded china": eggshell,
 * navy, cobalt, a gold data line). Day is `data-theme="light"` on <html>; every color is a token in
 * chartroom.css. One choice for the whole site, set from the Theme chooser on any page and kept per viewer.
 * An inline script in each page's <head> applies the saved choice before first paint (see THEME_BOOT).
 */
export type Theme = "dark" | "light";

const KEY = "csb-census:theme";
/** Fired on <html> after the theme changes, for what CSS cannot recolor (canvas, map styles, legends). */
export const THEME_EVENT = "csb-census:theme";

export function isTheme(v: unknown): v is Theme {
  return v === "dark" || v === "light";
}

export function currentTheme(): Theme {
  return document.documentElement.dataset.theme === "light" ? "light" : "dark";
}

export function savedTheme(): Theme {
  try {
    const v = localStorage.getItem(KEY);
    return isTheme(v) ? v : "dark";
  } catch {
    return "dark"; // storage unavailable: Night
  }
}

export function setTheme(theme: Theme): void {
  if (theme === currentTheme()) return;
  if (theme === "light") document.documentElement.dataset.theme = "light";
  else delete document.documentElement.dataset.theme;
  try {
    localStorage.setItem(KEY, theme);
  } catch {
    // storage unavailable (private window, blocked site data): the choice lasts for this page
  }
  document.documentElement.dispatchEvent(new CustomEvent(THEME_EVENT, { detail: theme }));
}

/** Runs `fn` after each theme change. */
export function onTheme(fn: (theme: Theme) => void): void {
  document.documentElement.addEventListener(THEME_EVENT, () => fn(currentTheme()));
}

/** A color token's current value, for drawing that CSS cannot reach (canvas, MapLibre paint). */
export function token(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

/** The Theme chooser (radios named "theme") in the masthead: checked to match, and switching on change. */
export function wireThemeChooser(): void {
  const theme = currentTheme();
  for (const input of document.querySelectorAll<HTMLInputElement>('input[name="theme"]')) {
    input.checked = input.value === theme;
    input.addEventListener("change", () => {
      if (isTheme(input.value)) setTheme(input.value);
    });
  }
}
