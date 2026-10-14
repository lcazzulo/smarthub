import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import HistoryPage from './HistoryPage';
const rooms = ['bedroom', 'bathroom'].map(id => ({ id, name: id, enabled: true, target_min_c: 4, target_max_c: 30, target_step_c: .5 }));
afterEach(() => vi.unstubAllGlobals());
function mockHistory() {
  const fetch = vi.fn().mockImplementation(async (url: string) => {
    const params = new URL(url, 'http://localhost').searchParams;
    return { ok: true, json: async () => ({ samples: [{ at: new Date(Date.parse(params.get('from')!) + 1000).toISOString(), temperature_c: url.includes('/outdoor/') ? -3 : 21, humidity_percent: 50, break_before: true }], next_cursor: null }) };
  });
  vi.stubGlobal('fetch', fetch); return fetch;
}
it('loads all three sensor series, compares samples, changes rooms and keeps fixed ranges', async () => {
  const fetch = mockHistory();
  const { rerender } = render(<HistoryPage rooms={rooms} initialRoom="bedroom"/>);
  await screen.findByRole('img', { name: 'Room humidity over selected time range' });
  expect(fetch).toHaveBeenCalledTimes(3);
  const bounds = fetch.mock.calls.map(([url]) => new URL(url, 'http://localhost').searchParams.get('from'));
  expect(new Set(bounds).size).toBe(1);
  const slider = screen.getByRole('slider');
  fireEvent.change(slider, { target: { value: Number(slider.getAttribute('min')) + 1000 } });
  expect(screen.getByText(/Room temperature: 21.0 °C/)).toBeInTheDocument();
  expect(screen.getByText(/Outside temperature: -3.0 °C/)).toBeInTheDocument();
  expect(screen.getByText(/Humidity: 50.0 %/)).toBeInTheDocument();
  rerender(<HistoryPage rooms={rooms} initialRoom="bathroom"/>);
  expect(fetch).toHaveBeenCalledTimes(3);
  fireEvent.change(screen.getByLabelText('Room'), { target: { value: 'bathroom' } });
  await screen.findByRole('img', { name: 'Room humidity over selected time range' });
  expect(fetch).toHaveBeenCalledTimes(6);
  expect(fetch.mock.calls[3][0]).toContain('/bathroom/');
  expect(new URL(fetch.mock.calls[3][0], 'http://localhost').searchParams.get('from')).toBe(bounds[0]);
});
it('validates custom dates before fetching and applies historical dates', async () => {
  const fetch = mockHistory();
  render(<HistoryPage rooms={rooms} initialRoom="bedroom"/>);
  await screen.findByRole('img', { name: 'Room humidity over selected time range' });
  fireEvent.change(screen.getByLabelText('From'), { target: { value: '2025-01-01T00:00' } });
  fireEvent.change(screen.getByLabelText('To'), { target: { value: '2025-03-01T00:00' } });
  fireEvent.click(screen.getByRole('button', { name: 'Apply range' }));
  expect(screen.getByRole('alert')).toHaveTextContent('31 days');
  expect(fetch).toHaveBeenCalledTimes(3);
  fireEvent.change(screen.getByLabelText('To'), { target: { value: '2025-01-08T00:00' } });
  fireEvent.click(screen.getByRole('button', { name: 'Apply range' }));
  await waitFor(() => expect(fetch).toHaveBeenCalledTimes(6));
  await screen.findByRole('img', { name: 'Room humidity over selected time range' });
  expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Yesterday' }));
  await waitFor(() => expect(fetch).toHaveBeenCalledTimes(9));
  await screen.findByRole('img', { name: 'Room humidity over selected time range' });
});
it('retries failures, displays empty history and cancels obsolete requests', async () => {
  const fetch = vi.fn().mockResolvedValueOnce({ ok: false }).mockResolvedValue({ ok: true, json: async () => ({ samples: [], next_cursor: null }) });
  vi.stubGlobal('fetch', fetch);
  const { unmount } = render(<HistoryPage rooms={rooms} initialRoom="bedroom"/>);
  await screen.findByRole('alert');
  fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
  await waitFor(() => expect(screen.getAllByText('No recorded samples in this time range.')).toHaveLength(2));
  expect(screen.queryByRole('slider')).not.toBeInTheDocument();
  const signal = fetch.mock.calls.at(-1)![1].signal;
  unmount(); expect(signal.aborted).toBe(true);
});
