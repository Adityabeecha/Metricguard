/** Light, dark, or follow the operating system. The choice is a per-browser convenience. */
export type ThemeChoice = 'light' | 'dark' | 'system';
export type Theme = 'light' | 'dark';

const KEY = 'mg-theme';
export const THEME_CHOICES: ThemeChoice[] = ['light', 'dark', 'system'];

export function parseThemeChoice(value: unknown): ThemeChoice {
  return value === 'light' || value === 'dark' || value === 'system' ? value : 'system';
}

export function resolveTheme(choice: ThemeChoice, systemDark: boolean): Theme {
  return choice === 'system' ? (systemDark ? 'dark' : 'light') : choice;
}

export function readThemeChoice(): ThemeChoice {
  try { return parseThemeChoice(localStorage.getItem(KEY)); } catch { return 'system'; }
}

export function saveThemeChoice(choice: ThemeChoice) {
  try { localStorage.setItem(KEY, choice); } catch { /* storage blocked: the choice lasts this visit */ }
}

const media = () => (typeof window !== 'undefined' && window.matchMedia ? window.matchMedia('(prefers-color-scheme: dark)') : null);

/** Sets data-theme on <html>; called before the first render so the page never flashes. */
export function applyTheme(choice: ThemeChoice) {
  const theme = resolveTheme(choice, Boolean(media()?.matches));
  document.documentElement.dataset.theme = theme;
  document.documentElement.style.colorScheme = theme;
}

/** Keeps "system" in step with the operating system while the page is open. */
export function watchSystemTheme(getChoice: () => ThemeChoice) {
  const query = media();
  if (!query) return () => {};
  const onChange = () => { if (getChoice() === 'system') applyTheme('system'); };
  query.addEventListener('change', onChange);
  return () => query.removeEventListener('change', onChange);
}
