import { useEffect, useMemo, useState } from 'react';
import { linePath, reducePoints, type Point } from './history';
import { loadSensorSeries, localInput, nearestPoint, presetRange, rangeError, type Range } from './archive';
import type { Room } from './types';
import './history.css';
import './archive.css';

const time = (at: number) => new Date(at).toLocaleString(undefined, { year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
type Series = { name: string; points: Point[]; className: string };
function ArchiveChart({ title, unit, series, range, cursor, inspect }: { title: string; unit: string; series: Series[]; range: Range; cursor: number | null; inspect: (at: number) => void }) {
  const reduced = useMemo(() => series.map(s => ({ ...s, points: reducePoints(s.points, range.from, range.to) })), [series, range]);
  let min = Infinity, max = -Infinity;
  for (const s of reduced) for (const p of s.points) if (p.temperature != null) { min = Math.min(min, p.temperature); max = Math.max(max, p.temperature); }
  const hasValues = Number.isFinite(min);
  min = hasValues ? Math.floor(min - 1) : 0; max = hasValues ? Math.ceil(max + 1) : unit === '%' ? 100 : 30;
  if (unit === '%') { min = Math.max(0, min); max = Math.min(100, max); }
  const x = (at: number) => 56 + (at - range.from) / (range.to - range.from) * 824;
  const y = (value: number) => 230 - (value - min) / (max - min) * 200;
  return <section className="panel archive-chart" aria-label={title}>
    <h3>{title}</h3>
    <div className="archive-legend">{series.map(s => <span key={s.name} className={s.className}>{s.name}{!s.points.length ? ' · No readings' : ''}</span>)}</div>
    {!hasValues ? <p className="history-message">No recorded samples in this time range.</p> : <>
      <svg className="history-chart" viewBox="0 0 920 278" role="img" aria-label={`${title} over selected time range`} onPointerMove={e => { const r = e.currentTarget.getBoundingClientRect(); if (r.width) inspect(range.from + Math.max(0, Math.min(1, ((e.clientX - r.left) / r.width * 920 - 56) / 824)) * (range.to - range.from)); }} onPointerDown={e => { const r = e.currentTarget.getBoundingClientRect(); if (r.width) inspect(range.from + Math.max(0, Math.min(1, ((e.clientX - r.left) / r.width * 920 - 56) / 824)) * (range.to - range.from)); }}>
        <text x="15" y="16">{unit}</text>
        {[0, 1, 2, 3, 4].map(i => { const v = min + (max - min) * i / 4; return <g key={i}><line className="grid-line" x1="56" x2="880" y1={y(v)} y2={y(v)}/><text x="45" y={y(v) + 4} textAnchor="end">{v.toFixed(1)}</text><text x={56 + i * 206} y="260" textAnchor={i === 0 ? 'start' : i === 4 ? 'end' : 'middle'}>{time(range.from + (range.to - range.from) * i / 4)}</text></g>; })}
        {reduced.map(s => <path key={s.name} d={linePath(s.points, 'temperature', x, y)} className={`archive-line ${s.className}`}/>)}
        {cursor != null && <line className="cursor-line" x1={x(cursor)} x2={x(cursor)} y1="30" y2="230"/>}
      </svg>
      <div className="history-readout" aria-live="polite">{cursor == null ? 'Hover, tap, or use the shared slider to compare readings.' : series.map(s => { const p = nearestPoint(s.points, cursor); return <p key={s.name}>{s.name}: {p?.temperature == null ? 'Unavailable' : `${p.temperature.toFixed(1)} ${unit} · ${time(p.at)}`}</p>; })}</div>
    </>}
  </section>;
}
export default function HistoryPage({ rooms, initialRoom }: { rooms: Room[]; initialRoom: string }) {
  const [room, setRoom] = useState(initialRoom || rooms[0]?.id || '');
  const [range, setRange] = useState<Range>(() => presetRange('week'));
  const [from, setFrom] = useState(() => localInput(range.from));
  const [to, setTo] = useState(() => localInput(range.to));
  const [preset, setPreset] = useState('week');
  const [validation, setValidation] = useState('');
  const [revision, setRevision] = useState(0);
  const [result, setResult] = useState<{ room: string; range: Range; series: Point[][] } | null>(null);
  const [error, setError] = useState('');
  const [cursor, setCursor] = useState<number | null>(null);
  useEffect(() => {
    if (!room) return;
    const abort = new AbortController();
    setResult(null); setError(''); setCursor(null);
    Promise.all((['temperature', 'outdoor', 'humidity'] as const).map(series => loadSensorSeries(room, series, range, abort.signal)))
      .then(series => { if (!abort.signal.aborted) setResult({ room, range, series }); })
      .catch(e => { if (!abort.signal.aborted) { setError(e.message); abort.abort(); } });
    return () => abort.abort();
  }, [room, range, revision]);
  const current = result?.room === room && result.range === range ? result : null;
  const temperatureSeries = useMemo(() => current ? [{ name: 'Room temperature', points: current.series[0], className: 'archive-room' }, { name: 'Outside temperature', points: current.series[1], className: 'archive-outdoor' }] : [], [current]);
  const humiditySeries = useMemo(() => current ? [{ name: 'Humidity', points: current.series[2], className: 'archive-humidity' }] : [], [current]);
  function choosePreset(value: string) { const next = presetRange(value); setPreset(value); setRange(next); setFrom(localInput(next.from)); setTo(localInput(next.to)); setValidation(''); }
  return <div className="archive-page">
    <div className="intro"><div className="eyebrow">Recorded climate</div><h2>History</h2><p>Compare your room’s temperature and humidity with the weather outside.</p></div>
    <section className="panel archive-filters" aria-label="History filters">
      <label>Room<select value={room} onChange={e => setRoom(e.target.value)}>{rooms.map(r => <option key={r.id} value={r.id}>{r.name === 'Livingroom' ? 'Living room' : r.name}</option>)}</select></label>
      <div className="history-ranges" role="group" aria-label="Date presets">{[['today', 'Today'], ['yesterday', 'Yesterday'], ['week', 'Last 7 days'], ['month', 'Last 30 days']].map(([value, label]) => <button key={value} aria-pressed={preset === value} onClick={() => choosePreset(value)}>{label}</button>)}</div>
      <form onSubmit={e => { e.preventDefault(); const next = { from: new Date(from).getTime(), to: new Date(to).getTime() }; const message = rangeError(next.from, next.to); setValidation(message); if (!message) { setPreset('custom'); setRange(next); } }}>
        <label>From<input type="datetime-local" required value={from} onChange={e => setFrom(e.target.value)}/></label>
        <label>To<input type="datetime-local" required value={to} onChange={e => setTo(e.target.value)}/></label>
        <button className="archive-action" type="submit">Apply range</button>
      </form>
      {validation && <p role="alert">{validation}</p>}
      <p className="history-note-inline">Select up to 31 days at a time, including older dates. Dates and times use {Intl.DateTimeFormat().resolvedOptions().timeZone}. Historical views stay fixed.</p>
    </section>
    <div className="archive-summary"><p>{time(range.from)} — {time(range.to)}</p><button className="archive-action" onClick={() => setRevision(v => v + 1)}>Refresh history</button></div>
    {error ? <p role="alert" className="panel history-message">{error} <button onClick={() => setRevision(v => v + 1)}>Retry</button></p> : !current ? <p role="status" className="panel history-message">Loading recorded history…</p> : <>
      <ArchiveChart title="Room and outside temperature" unit="°C" series={temperatureSeries} range={range} cursor={cursor} inspect={setCursor}/>
      <ArchiveChart title="Room humidity" unit="%" series={humiditySeries} range={range} cursor={cursor} inspect={setCursor}/>
      {current.series.some(s => s.length > 0) && <div className="panel"><label className="history-scrubber">Compare at a time<input aria-label="Inspect historical time" type="range" min={range.from} max={range.to} step="1000" value={cursor ?? range.from} onChange={e => setCursor(Number(e.target.value))}/></label><p className="history-note">{cursor == null ? 'Move the slider to inspect both charts.' : `Selected time: ${time(cursor)}`} Values show the nearest recorded sample and its actual timestamp; sensors may report at different times. Line breaks mark recorder restarts.</p></div>}
    </>}
  </div>;
}
