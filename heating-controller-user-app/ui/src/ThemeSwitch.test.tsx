import { fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import ThemeSwitch from './ThemeSwitch';

beforeEach(() => { localStorage.clear(); delete document.documentElement.dataset.theme; });
afterEach(() => { localStorage.clear(); delete document.documentElement.dataset.theme; vi.unstubAllGlobals(); });
it('uses the system preference initially and remembers a manual choice across mounts', () => {
  vi.stubGlobal('matchMedia', vi.fn().mockReturnValue({ matches: true }));
  const { unmount } = render(<ThemeSwitch/>);
  const toggle = screen.getByRole('switch', { name: 'Dark mode' });
  expect(toggle).toHaveAttribute('aria-checked', 'true');
  expect(document.documentElement).toHaveAttribute('data-theme', 'dark');
  fireEvent.click(toggle);
  expect(document.documentElement).toHaveAttribute('data-theme', 'light');
  expect(localStorage.getItem('heating-theme')).toBe('light');
  unmount();
  render(<ThemeSwitch/>);
  expect(screen.getByRole('switch')).toHaveAttribute('aria-checked', 'false');
  fireEvent.click(screen.getByRole('switch'));
  expect(document.documentElement).toHaveAttribute('data-theme', 'dark');
});
it('still switches when browser storage is unavailable', () => {
  vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('Storage blocked'); });
  vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('Storage blocked'); });
  render(<ThemeSwitch/>);
  fireEvent.click(screen.getByRole('switch'));
  expect(document.documentElement).toHaveAttribute('data-theme', 'dark');
});
