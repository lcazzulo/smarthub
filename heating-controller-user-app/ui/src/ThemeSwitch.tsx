import { useLayoutEffect, useState } from 'react';
import { Moon, Sun } from 'lucide-react';

type Theme = 'light' | 'dark';
function initialTheme(): Theme {
  try {
    const saved = localStorage.getItem('heating-theme');
    if (saved === 'light' || saved === 'dark') return saved;
  } catch { /* Storage may be disabled; the switch still works for this visit. */ }
  return window.matchMedia?.('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
}

export default function ThemeSwitch() {
  const [theme, setTheme] = useState<Theme>(initialTheme);
  useLayoutEffect(() => { document.documentElement.dataset.theme = theme; }, [theme]);
  function toggle() {
    const next = theme === 'dark' ? 'light' : 'dark';
    setTheme(next);
    try { localStorage.setItem('heating-theme', next); } catch { /* Keep the in-memory preference. */ }
  }
  return <button type="button" className="theme-switch" role="switch" aria-label="Dark mode" aria-checked={theme === 'dark'} onClick={toggle}>
    {theme === 'dark' ? <Moon size={17} aria-hidden="true"/> : <Sun size={17} aria-hidden="true"/>}
    <span>{theme === 'dark' ? 'Dark' : 'Light'}</span><span className="theme-track" aria-hidden="true"><span/></span>
  </button>;
}
