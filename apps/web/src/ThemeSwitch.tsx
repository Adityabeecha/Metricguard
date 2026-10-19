import { useEffect, useState } from 'react';
import { Monitor, Moon, Sun } from 'lucide-react';
import { applyTheme, readThemeChoice, saveThemeChoice, THEME_CHOICES, type ThemeChoice } from './themeChoice';

const LABEL: Record<ThemeChoice, string> = { light: 'Light', dark: 'Dark', system: 'System' };
const ICON = { light: Sun, dark: Moon, system: Monitor };
const EVENT = 'mg-theme-change';

/** Light / Dark / System. `compact` shows icons only (top bar); otherwise icons with labels (Settings). */
export function ThemeSwitch({ compact = false }: { compact?: boolean }) {
  const [choice, setChoice] = useState<ThemeChoice>(readThemeChoice);
  // Several switches can be on screen (top bar and Settings); keep them in step.
  useEffect(() => {
    const sync = () => setChoice(readThemeChoice());
    window.addEventListener(EVENT, sync);
    return () => window.removeEventListener(EVENT, sync);
  }, []);
  const choose = (next: ThemeChoice) => {
    saveThemeChoice(next);
    applyTheme(next);
    setChoice(next);
    window.dispatchEvent(new Event(EVENT));
  };
  return (
    <div className={`theme-switch ${compact ? 'compact' : ''}`} role="radiogroup" aria-label="Theme">
      {THEME_CHOICES.map(c => {
        const Icon = ICON[c];
        return (
          <button key={c} type="button" role="radio" aria-checked={choice === c} title={compact ? `${LABEL[c]} theme` : undefined}
            aria-label={compact ? `${LABEL[c]} theme` : undefined} onClick={() => choose(c)}>
            <Icon size={14} aria-hidden="true" />{!compact && <span>{LABEL[c]}</span>}
          </button>
        );
      })}
    </div>
  );
}
