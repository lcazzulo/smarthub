import type { Snapshot } from './types';
export interface Point { at: number; temperature: number | null; target: number | null; breakBefore: boolean; live?: boolean; p?: number | null; i?: number | null; opening?: number | null; status?: string; supply?: boolean | null; dryRun?: boolean }
export interface HistorySample { at: string; temperature_c: number | null; target_temperature_c: number | null; break_before: boolean; control_status: string; proportional_percent?: number | null; integral_percent?: number | null; requested_opening_percent?: number | null; supply_available?: boolean | number | null; dry_run?: boolean }
export function historyPoint(row: HistorySample): Point {
  return { at: Date.parse(row.at), temperature: ['stale_temperature', 'waiting_for_temperature'].includes(row.control_status) ? null : row.temperature_c,
    p: row.proportional_percent ?? null, i: row.integral_percent ?? null,
    opening: row.requested_opening_percent ?? null, status: row.control_status, supply: row.supply_available == null ? null : !!row.supply_available, dryRun: row.dry_run,
    target: row.target_temperature_c, breakBefore: row.break_before };
}
export async function loadHistory(room: string, from: number, to: number, signal: AbortSignal): Promise<Point[]> {
  const points: Point[] = [];
  let cursor: string | null = null;
  const seen = new Set<string>();
  do {
    const params = new URLSearchParams({ from: new Date(from).toISOString(), to: new Date(to).toISOString(), series: 'control', limit: '5000' });
    if (cursor) params.set('cursor', cursor);
    const response = await fetch(`/api/v1/rooms/${encodeURIComponent(room)}/history?${params}`, { signal });
    if (!response.ok) throw new Error('Recorded history is unavailable. Please try again.');
    const page = await response.json();
    if (!Array.isArray(page.samples)) throw new Error('Unexpected history response.');
    const runs = new Map<number, boolean>((page.runs ?? []).map((run: {id:number;dry_run:boolean}) => [run.id, run.dry_run]));
    points.push(...page.samples.map((row: HistorySample & {run_id:number}) => historyPoint({...row,dry_run:runs.get(row.run_id)})));
    cursor = page.next_cursor;
    if (cursor && (seen.has(cursor) || points.length >= 250000)) throw new Error('This range contains too many samples. Select a shorter range.');
    if (cursor) seen.add(cursor);
  } while (cursor && !signal.aborted);
  if (signal.aborted) throw new DOMException('Aborted', 'AbortError');
  return points;
}
export function livePoint(snapshot: Snapshot): Point | null {
  const t = snapshot.telemetry;
  if (!snapshot.available || snapshot.stale || !snapshot.received_at || !t) return null;
  return { at: Date.parse(snapshot.received_at), temperature: ['stale_temperature', 'waiting_for_temperature'].includes(t.status) ? null : t.temperature_c,
    p: t.proportional_percent, i: t.integral_percent,
    opening: t.opening_percent, status: t.status, supply: t.status === 'outside_supply' ? false : null, dryRun: t.dry_run,
    target: t.target_temperature_c, breakBefore: false, live: true };
}
// Keep first/last and extrema of all series per time bucket; retain every gap.
export function reducePoints(points: Point[], from: number, to: number, buckets = 700): Point[] {
  const result: Point[] = [];
  let group: Point[] = [], bucket = -1;
  function flush() {
    if (!group.length) return;
    const selected = new Set([0, group.length - 1]);
    for (const key of ['temperature', 'target', 'opening', 'p', 'i'] as const) {
      let min = -1, max = -1;
      group.forEach((p, i) => { if (p[key] != null) { if (min < 0 || p[key]! < group[min][key]!) min = i; if (max < 0 || p[key]! > group[max][key]!) max = i; } });
      if (min >= 0) { selected.add(min); selected.add(max); }
    }
    [...selected].sort((a,b)=>a-b).forEach(i=>result.push(group[i])); group=[];
  }
  for (const p of points) {
    const next = Math.floor((p.at-from) / Math.max(1,to-from) * buckets);
    const previous = group[group.length-1];
    if (next !== bucket || p.breakBefore || (previous && ((previous.temperature == null) !== (p.temperature == null) || (previous.target == null) !== (p.target == null) || (previous.opening == null) !== (p.opening == null) || (previous.p == null) !== (p.p == null) || (previous.i == null) !== (p.i == null) || previous.status !== p.status || previous.supply !== p.supply || previous.dryRun !== p.dryRun))) flush();
    bucket=next; group.push(p);
  }
  flush(); return result;
}
export function linePath(points: Point[], key: 'temperature' | 'target' | 'opening' | 'p' | 'i', x: (v: number)=>number, y: (v:number)=>number): string {
  let active=false;
  return points.map(p=>{
    const value=p[key];
    if (value == null || !Number.isFinite(value)) { active=false; return ''; }
    const move=!active || p.breakBefore; active=true;
    return move ? `M${x(p.at)},${y(value)}h0` : key !== 'temperature' ? `H${x(p.at)}V${y(value)}` : `L${x(p.at)},${y(value)}`;
  }).join(' ');
}

export interface StatusBand { from: number; to: number; label: string; tone: string }
export function statusBands(points: Point[]): StatusBand[] {
  const bands: StatusBand[] = [];
  for (let i=0;i<points.length-1;i++) {
    const p=points[i], next=points[i+1];
    if(next.breakBefore || next.at<=p.at) continue;
    const label=p.status?.replaceAll('_',' ') ?? 'Unknown status';
    const tone=p.status==='outside_supply'?'supply':p.status==='outside_heating_schedule'?'schedule':p.status==='active'?'active':'other';
    const description=`${label}${p.supply===false?' · supply unavailable':p.supply===true?' · supply scheduled':' · supply unknown'}${p.dryRun?' · dry run':''}`;
    const last=bands.at(-1);
    if(last && last.to===p.at && last.label===description)last.to=next.at;
    else bands.push({from:p.at,to:next.at,label:description,tone});
  }
  return bands;
}
