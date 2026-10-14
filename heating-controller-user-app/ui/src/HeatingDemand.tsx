import DemandHeatmap, { type HeatCell } from './DemandHeatmap';
import { useEffect, useState } from 'react';
import type { Room } from './types';
import './demand.css';

interface Day {
  timeline?: HeatCell[];
  date: string; day_seconds: number; elapsed_seconds: number; partial: boolean;
  bands_seconds: number[]; coverage_seconds: number; simulated_seconds: number;
  equivalent_seconds: number; active_seconds: number; average_opening_percent: number | null; unknown_seconds: number;
}
const labels = ['>0–25%', '>25–50%', '>50–75%', '>75–100%'];
const duration = (seconds: number) => `${(seconds / 3600).toFixed(2)} h`;
function today() { return new Intl.DateTimeFormat('en-CA', { timeZone: 'Europe/Rome', year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date()); }
function offset(day: string, days: number) { const date = new Date(`${day}T12:00:00Z`); date.setUTCDate(date.getUTCDate() + days); return date.toISOString().slice(0, 10); }
function initialRange(count = 7) { const to = today(); return { from: offset(to, 1 - count), to }; }
export default function HeatingDemand({ rooms, initialRoom }: { rooms: Room[]; initialRoom: string }) {
  const [room, setRoom] = useState(initialRoom || rooms[0]?.id || '');
  const [range, setRange] = useState(initialRange);
  const [from, setFrom] = useState(range.from), [to, setTo] = useState(range.to);
  const [validation, setValidation] = useState('');
  const [revision, setRevision] = useState(0);
  const [result, setResult] = useState<{ room: string; range: typeof range; days: Day[] } | null>(null);
  const [error, setError] = useState('');
  const [selected, setSelected] = useState(0);
  useEffect(() => {
    const abort = new AbortController();
    setResult(null); setError(''); setSelected(0);
    fetch(`/api/v1/rooms/${encodeURIComponent(room)}/demand?${new URLSearchParams(range)}`, { signal: abort.signal })
      .then(async response => { if (!response.ok) throw new Error('Heating demand history is unavailable. Try a shorter range or retry.'); const data = await response.json(); if (!Array.isArray(data.days)) throw new Error('Unexpected demand response.'); return data.days as Day[]; })
      .then(days => { if (!abort.signal.aborted) setResult({ room, range, days }); })
      .catch(e => { if (!abort.signal.aborted) setError(e.message); });
    return () => abort.abort();
  }, [room, range, revision]);
  const days = result?.room === room && result.range === range ? result.days : null;
  const chosen = days?.[selected];
  const maximum = Math.max(1, ...(days ?? []).map(day => Math.ceil(day.active_seconds / 3600)));
  function preset(count: number) { const next = initialRange(count); setRange(next); setFrom(next.from); setTo(next.to); setValidation(''); }
  return <div className="demand-page">
    <div className="intro"><div className="eyebrow">Daily room activity</div><h2>Heating demand</h2><p>How long your room requested heat, grouped by valve opening.</p></div>
    <section className="panel archive-filters" aria-label="Heating demand filters">
      <label>Room<select value={room} onChange={e => setRoom(e.target.value)}>{rooms.map(r => <option key={r.id} value={r.id}>{r.name === 'Livingroom' ? 'Living room' : r.name}</option>)}</select></label>
      <div className="history-ranges" role="group" aria-label="Demand date presets">{[7, 14, 30].map(n => <button key={n} onClick={() => preset(n)}>Last {n} days</button>)}</div>
      <form onSubmit={e => { e.preventDefault(); const span = (Date.parse(to) - Date.parse(from)) / 86400000; if (!Number.isFinite(span) || span < 0 || span >= 31 || to > today()) { setValidation('Choose 1–31 days, with the end on or after the start and no later than today.'); return; } setValidation(''); setRange({ from, to }); }}>
        <label>From<input type="date" required value={from} max={today()} onChange={e => setFrom(e.target.value)}/></label>
        <label>To<input type="date" required value={to} max={today()} onChange={e => setTo(e.target.value)}/></label>
        <button className="archive-action" type="submit">Apply dates</button>
      </form>
      {validation && <p role="alert">{validation}</p>}
      <p className="history-note-inline">Inclusive calendar dates · Europe/Rome · Up to 31 days per view.</p>
    </section>
    <div className="archive-summary"><p>{range.from} — {range.to}</p><button className="archive-action" onClick={() => setRevision(v => v + 1)}>Refresh demand</button></div>
    {error ? <p role="alert" className="panel history-message">{error} <button onClick={() => setRevision(v => v + 1)}>Retry</button></p> : !days ? <p role="status" className="panel history-message">Loading daily heating demand…</p> : <>
      <section className="panel" aria-label="Daily heating demand chart">
        <h3>Hours requesting heat</h3>
        <div className="demand-legend">{labels.map((label, i) => <span key={label}><i className={`demand-band-${i}`}/>{label} opening</span>)}</div>
        {!days.some(day => day.coverage_seconds > 0) && <p className="history-message">No non-simulated recording coverage in this range.</p>}
        <p className="history-note-inline">Scale: 0–{maximum} hours. Select a day for details. * marks incomplete coverage or a day still in progress.</p>
        <div className="demand-scroll"><div className="demand-bars" style={{ minWidth: Math.max(320, days.length * 44) }}>{days.map((day, index) => <button key={day.date} className="demand-day" aria-label={`${day.date}: ${duration(day.active_seconds)} requested heating${day.partial || day.unknown_seconds > 1 || day.simulated_seconds > 0 ? ', incomplete coverage' : ''}`} aria-pressed={selected === index} onClick={() => setSelected(index)} onPointerEnter={() => setSelected(index)} onFocus={() => setSelected(index)}>
          <span className="demand-total">{(day.active_seconds / 3600).toFixed(1)}</span>
          <span className="demand-column">{day.bands_seconds.map((seconds, i) => <span key={i} className={`demand-band-${i}`} style={{ height: `${seconds / 3600 / maximum * 100}%` }}/>)}</span>
          <span className="demand-date">{day.date.slice(5)}{day.partial || day.unknown_seconds > 1 || day.simulated_seconds > 0 ? '*' : ''}</span>
        </button>)}</div></div>
      </section>
      <DemandHeatmap days={days} selectedDay={selected} onSelectDay={setSelected}/>
      {chosen && <section className="panel demand-details" aria-label="Selected day details" aria-live="polite">
        <h3>{chosen.date}{chosen.partial ? ' · In progress' : ''}</h3>
        <dl><div><dt>Requested heating</dt><dd>{duration(chosen.active_seconds)}</dd></div><div><dt>Average opening while active</dt><dd>{chosen.average_opening_percent == null ? '—' : `${chosen.average_opening_percent.toFixed(1)}%`}</dd></div><div><dt>Full-opening equivalent</dt><dd>{duration(chosen.equivalent_seconds)}</dd></div><div><dt>Recorded coverage</dt><dd>{duration(chosen.coverage_seconds)} of {duration(chosen.elapsed_seconds)} elapsed</dd></div><div><dt>Unknown / missing data</dt><dd>{duration(chosen.unknown_seconds)}</dd></div><div><dt>Simulation excluded</dt><dd>{duration(chosen.simulated_seconds)}</dd></div></dl>
        <ul className="demand-breakdown">{labels.map((label, i) => <li key={label}><i className={`demand-band-${i}`}/>{label}: {duration(chosen.bands_seconds[i])}</li>)}</ul>
        <p className="history-note">Day length: {duration(chosen.day_seconds)}. Requested valve opening does not confirm boiler operation or heat delivered. Full-opening equivalent weights time by opening; it is not energy consumption.</p>
      </section>}
      <p className="history-note">Durations use consecutive control samples from the same recorder run. Gaps longer than three control periods and the unconfirmed tail after the last sample remain unknown. Simulated demand is excluded. Days follow Europe/Rome, including daylight-saving changes.</p>
    </>}
  </div>;
}
