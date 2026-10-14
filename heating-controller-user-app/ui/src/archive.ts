import type { Point } from './history';

export const MAX_RANGE = 31 * 86400000;
export type Range = { from: number; to: number };
export function presetRange(preset: string, now = new Date()): Range {
  const to = now.getTime();
  const midnight = new Date(now); midnight.setHours(0, 0, 0, 0);
  if (preset === 'today') return { from: midnight.getTime(), to };
  if (preset === 'yesterday') {
    const yesterday = new Date(midnight); yesterday.setDate(yesterday.getDate() - 1);
    return { from: yesterday.getTime(), to: midnight.getTime() };
  }
  return { from: to - (preset === 'month' ? 30 : 7) * 86400000, to };
}
export function localInput(at: number): string {
  const d = new Date(at);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}T${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
}
export function rangeError(from: number, to: number): string {
  if (!Number.isFinite(from) || !Number.isFinite(to)) return 'Enter both a start and end date.';
  if (to <= from) return 'The end must be after the start.';
  if (to - from > MAX_RANGE) return 'Choose up to 31 days at a time. You can select older dates.';
  if (to > Date.now()) return 'The end cannot be in the future.';
  return '';
}
export async function loadSensorSeries(room: string, series: 'temperature' | 'humidity' | 'outdoor', range: Range, signal: AbortSignal): Promise<Point[]> {
  const points: Point[] = [], seen = new Set<string>();
  let cursor: string | null = null;
  do {
    const params = new URLSearchParams({ from: new Date(range.from).toISOString(), to: new Date(range.to).toISOString(), limit: '5000' });
    if (series !== 'outdoor') params.set('series', series);
    if (cursor) params.set('cursor', cursor);
    const path = series === 'outdoor' ? '/api/v1/outdoor/history' : `/api/v1/rooms/${encodeURIComponent(room)}/history`;
    const response = await fetch(`${path}?${params}`, { signal });
    if (!response.ok) throw new Error('Recorded history is unavailable. Please try again.');
    const page = await response.json();
    if (!Array.isArray(page.samples)) throw new Error('Unexpected history response.');
    for (const row of page.samples) {
      const at = Date.parse(row.at), value = series === 'humidity' ? row.humidity_percent : row.temperature_c;
      if (!Number.isFinite(at) || at < range.from || at >= range.to) throw new Error('Unexpected history timestamp.');
      points.push({ at, temperature: typeof value === 'number' && Number.isFinite(value) ? value : null, target: null, breakBefore: !!row.break_before });
    }
    cursor = page.next_cursor;
    if (cursor && (seen.has(cursor) || points.length >= 250000)) throw new Error('Too many readings. Choose a shorter time range.');
    if (cursor) seen.add(cursor);
  } while (cursor && !signal.aborted);
  if (signal.aborted) throw new DOMException('Aborted', 'AbortError');
  return points;
}
export function nearestPoint(points: Point[], at: number): Point | undefined {
  if (!points.length) return;
  let low = 0, high = points.length - 1;
  while (low < high) { const middle = Math.floor((low + high) / 2); if (points[middle].at < at) low = middle + 1; else high = middle; }
  return low > 0 && at - points[low - 1].at < points[low].at - at ? points[low - 1] : points[low];
}
