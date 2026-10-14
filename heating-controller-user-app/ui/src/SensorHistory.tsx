import { useEffect, useRef, useState } from 'react';
import { linePath, reducePoints, type Point } from './history';
import './history.css';

const time = (at: number) => new Date(at).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });

export default function SensorHistory({ room, reading, online = false, streamId = null, onClose }: { room?: { id: string; name: string }; reading?: { value: number; at: string } | null; online?: boolean; streamId?: string | null; onClose: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [hours, setHours] = useState(24);
  const [follow, setFollow] = useState(true);
  const pending = useRef<Point[]>([]);
  const boundary = useRef(0);
  const lastSeen = useRef(0);
  const broken = useRef(true);
  const connection = useRef({ online, streamId });
  const [revision, setRevision] = useState(0);
  const [data, setData] = useState<{ points: Point[]; from: number; to: number } | null>(null);
  const [error, setError] = useState('');
  const [cursor, setCursor] = useState<number | null>(null);
  const title = room ? `${room.name} humidity history` : 'Outside temperature history';
  const unit = room ? '%' : '°C';
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    dialog.current?.showModal();
    const overflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => { document.body.style.overflow = overflow; previous?.focus(); };
  }, []);
  useEffect(() => {
    if (online && (!connection.current.online || connection.current.streamId !== streamId) && follow) setRevision(v => v + 1);
    if (!online || connection.current.streamId !== streamId) broken.current = true;
    connection.current = { online, streamId };
  }, [online, streamId, follow]);
  useEffect(() => {
    const abort = new AbortController();
    const to = Date.now(), from = to - hours * 3600000;
    pending.current = []; boundary.current = to; lastSeen.current = 0; broken.current = true;
    setData(null); setError(''); setCursor(null);
    async function load() {
      const points: Point[] = [], seen = new Set<string>();
      let cursor: string | null = null;
      do {
        const params = new URLSearchParams({ from: new Date(from).toISOString(), to: new Date(to).toISOString(), limit: '5000' });
        if (room) params.set('series', 'humidity');
        if (cursor) params.set('cursor', cursor);
        const response = await fetch(`${room ? `/api/v1/rooms/${encodeURIComponent(room.id)}/history` : '/api/v1/outdoor/history'}?${params}`, { signal: abort.signal });
        if (!response.ok) throw new Error('Recorded history is unavailable. Please try again.');
        const page = await response.json();
        if (!Array.isArray(page.samples)) throw new Error('Unexpected history response.');
        for (const row of page.samples) points.push({ at: Date.parse(row.at), temperature: room ? row.humidity_percent : row.temperature_c, target: null, breakBefore: row.break_before });
        cursor = page.next_cursor;
        if (cursor && (seen.has(cursor) || points.length >= 250000)) throw new Error('Too many samples. Select a shorter range.');
        if (cursor) seen.add(cursor);
      } while (cursor && !abort.signal.aborted);
      if (!abort.signal.aborted) {
        const tail = pending.current;
        const end = Math.max(to, tail.at(-1)?.at ?? to);
        setData({ points: [...points, ...tail].filter(p => p.at >= end - hours * 3600000), from: end - hours * 3600000, to: end });
        pending.current = [];
      }
    }
    load().catch(e => { if (!abort.signal.aborted) setError(e.message); });
    return () => abort.abort();
  }, [room?.id, hours, revision]);
  useEffect(() => {
    if (!follow || !online) return;
    const at = reading ? Date.parse(reading.at) : NaN;
    if (!reading || !Number.isFinite(reading.value) || !Number.isFinite(at)) { broken.current = true; return; }
    // History ends exclusively at the request boundary. Cached/repeated readings
    // must not become new live samples when unrelated socket events arrive.
    if (at < boundary.current || at <= lastSeen.current) return;
    lastSeen.current = at;
    const point: Point = { at, temperature: reading.value, target: null, live: true, breakBefore: broken.current };
    broken.current = false;
    pending.current.push(point);
    if (pending.current.length > 25000) pending.current.shift();
    setData(old => {
      if (!old) return old; // Retain readings arriving during the history request.
      return { points: [...old.points.filter(p => p.at >= at - hours * 3600000), point], from: at - hours * 3600000, to: at };
    });
  }, [reading?.at, reading?.value, online, follow, hours, revision, room?.id, streamId]);
  const points = data?.points ?? [];
  const reduced = data ? reducePoints(points, data.from, data.to) : [];
  const values = reduced.map(p => p.temperature).filter((v): v is number => v != null && Number.isFinite(v));
  const min = values.length ? Math.floor(Math.min(...values) - 1) : 0;
  const max = values.length ? Math.ceil(Math.max(...values) + 1) : 100;
  const x = (at: number) => 56 + (at - data!.from) / (data!.to - data!.from) * 824;
  const y = (v: number) => 230 - (v - min) / (max - min) * 200;
  const selected = cursor == null ? null : points[Math.min(cursor, points.length - 1)];
  function inspect(clientX: number, left: number, width: number) {
    if (!data || !points.length || !width) return;
    const at = data.from + Math.max(0, Math.min(1, ((clientX - left) / width * 920 - 56) / 824)) * (data.to - data.from);
    let nearest = 0;
    points.forEach((p, i) => { if (Math.abs(p.at - at) < Math.abs(points[nearest].at - at)) nearest = i; });
    setCursor(nearest);
  }
  return <dialog ref={dialog} className="sensor-history panel" aria-labelledby="sensor-history-title" onCancel={onClose} onClick={e => { if (e.target === e.currentTarget) { const r = e.currentTarget.getBoundingClientRect(); if (e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom) onClose(); } }}>
    <div className="history-heading"><h2 id="sensor-history-title">{title}</h2><button className="history-close" onClick={onClose} aria-label="Close history">Close ×</button></div>
    <div className="history-options"><div className="history-ranges" role="group" aria-label="History time range">{[1, 6, 24, 168].map(h => <button key={h} aria-pressed={hours === h} onClick={() => setHours(h)}>{h === 168 ? '7d' : `${h}h`}</button>)}</div><label><input type="checkbox" checked={follow} onChange={e => { setFollow(e.target.checked); if (e.target.checked) setRevision(v => v + 1); }}/> Follow live</label><button onClick={() => setRevision(v => v + 1)}>Refresh</button></div>
    {!data && !error && <p role="status" className="history-message">Loading recorded history…</p>}
    {error && <p role="alert" className="history-message">{error} <button onClick={() => setRevision(v => v + 1)}>Retry</button></p>}
    {data && !points.length && <p className="history-message">No recorded samples in this time range.</p>}
    {data && points.length > 0 && <>
      <svg viewBox="0 0 920 278" className="history-chart" role="img" aria-label={`${room ? 'Humidity' : 'Outside temperature'} in ${room ? 'percent' : 'degrees Celsius'} over time`} onPointerMove={e => { const r = e.currentTarget.getBoundingClientRect(); inspect(e.clientX, r.left, r.width); }} onPointerDown={e => { const r = e.currentTarget.getBoundingClientRect(); inspect(e.clientX, r.left, r.width); }}>
        <text x="15" y="16">{unit}</text>
        {[0, 1, 2, 3, 4].map(i => { const v = min + (max - min) * i / 4; return <g key={i}><line x1="56" x2="880" y1={y(v)} y2={y(v)} className="grid-line"/><text x="45" y={y(v) + 4} textAnchor="end">{v.toFixed(1)}</text><text x={56 + 824 * i / 4} y="260" textAnchor={i === 0 ? 'start' : i === 4 ? 'end' : 'middle'}>{time(data.from + (data.to - data.from) * i / 4)}</text></g>; })}
        <path d={linePath(reduced, 'temperature', x, y)} className="actual-line"/>
        {selected && <line x1={x(selected.at)} x2={x(selected.at)} y1="30" y2="230" className="cursor-line"/>}
      </svg>
      <label className="history-scrubber">Inspect a sample<input aria-label="Inspect sensor history sample" type="range" min={0} max={points.length - 1} value={Math.min(cursor ?? points.length - 1, points.length - 1)} onChange={e => setCursor(Number(e.target.value))}/></label>
      <p className="history-readout" aria-live="polite">{selected ? `${time(selected.at)} · ${selected.temperature?.toFixed(1) ?? 'Unavailable'} ${unit}${selected.live ? ' · Live telemetry' : ''}` : 'Hover, tap, or use the slider to inspect a sample.'}</p>
    </>}
    <p className="history-note">Recorded sensor readings · Times shown in your local timezone. {follow ? (online ? 'Following live telemetry.' : 'Waiting for connection.') : 'Live updates paused.'}</p>
  </dialog>;
}
