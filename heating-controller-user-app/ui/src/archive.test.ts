import { afterEach, expect, it, vi } from 'vitest';
import { loadSensorSeries, localInput, MAX_RANGE, nearestPoint, presetRange, rangeError } from './archive';
afterEach(() => vi.unstubAllGlobals());
it('builds calendar presets and validates historical windows', () => {
  const now = new Date(2026, 9, 14, 13, 45);
  const today = presetRange('today', now), yesterday = presetRange('yesterday', now);
  expect(localInput(today.from)).toBe('2026-10-14T00:00');
  expect(today.to).toBe(now.getTime());
  expect(localInput(yesterday.from)).toBe('2026-10-13T00:00');
  expect(yesterday.to).toBe(today.from);
  expect(rangeError(0, MAX_RANGE)).toBe('');
  expect(rangeError(0, MAX_RANGE + 1)).toContain('31 days');
  expect(rangeError(10, 9)).toContain('after');
  expect(rangeError(NaN, 10)).toContain('both');
  expect(rangeError(Date.now(), Date.now() + 10000)).toContain('future');
});
it('follows pagination with fixed bounds and preserves gaps', async () => {
  const fetch = vi.fn().mockResolvedValueOnce({ ok: true, json: async () => ({ samples: [{ at: new Date(1000).toISOString(), humidity_percent: 0, break_before: true }], next_cursor: 'next' }) }).mockResolvedValueOnce({ ok: true, json: async () => ({ samples: [{ at: new Date(2000).toISOString(), humidity_percent: 55, break_before: false }], next_cursor: null }) });
  vi.stubGlobal('fetch', fetch);
  const result = await loadSensorSeries('bed room', 'humidity', { from: 0, to: 3000 }, new AbortController().signal);
  expect(result.map(p => p.temperature)).toEqual([0, 55]);
  expect(result.map(p => p.breakBefore)).toEqual([true, false]);
  expect(fetch.mock.calls[0][0]).toContain('/bed%20room/history?');
  expect(fetch.mock.calls[1][0]).toBe(`${fetch.mock.calls[0][0]}&cursor=next`);
  expect(nearestPoint(result, 1900)?.temperature).toBe(55);
});
it('rejects cursor loops and honors cancellation', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => ({ samples: [], next_cursor: 'loop' }) }));
  await expect(loadSensorSeries('room', 'outdoor', { from: 0, to: 3000 }, new AbortController().signal)).rejects.toThrow('shorter');
  const abort = new AbortController(); abort.abort();
  await expect(loadSensorSeries('room', 'temperature', { from: 0, to: 3000 }, abort.signal)).rejects.toThrow('Aborted');
});
