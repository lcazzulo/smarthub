import { useId, useState } from 'react';

export interface HeatCell {
  minute: number; fold: number; offset: string; repeated: boolean;
  coverage_seconds: number; active_seconds: number; simulated_seconds: number;
  elapsed_seconds: number; unknown_seconds: number; opening_percent: number | null;
}
export interface HeatDay { date: string; timeline?: HeatCell[] }
const clock = (minute: number) => `${String(Math.floor(minute / 60)).padStart(2, '0')}:${String(minute % 60).padStart(2, '0')}`;
export function openingColor(value: number) {
  // Blue to red through violet, with a consistent scale across days and themes.
  const fraction = Math.max(0, Math.min(100, value)) / 100;
  return `rgb(${Math.round(45 + 185 * fraction)}, ${Math.round(110 - 50 * fraction)}, ${Math.round(230 - 175 * fraction)})`;
}
function description(day: HeatDay, cell: HeatCell) {
  const minutes = (seconds: number) => `${(seconds / 60).toFixed(1)} min`;
  return `${day.date} · ${clock(cell.minute)}–${clock(cell.minute + 5)} · UTC${cell.offset.slice(0, 3)}:${cell.offset.slice(3)}${cell.repeated ? (cell.fold ? ' · Second occurrence' : ' · First occurrence') : ''} · ${cell.opening_percent == null ? 'No requested heating' : `Average requested opening while active: ${cell.opening_percent.toFixed(1)}%`} · Active ${minutes(cell.active_seconds)} · Closed ${minutes(Math.max(0, cell.coverage_seconds - cell.active_seconds))} · Missing ${minutes(cell.unknown_seconds)} · Simulation ${minutes(cell.simulated_seconds)} · Not yet elapsed ${minutes(300 - cell.elapsed_seconds)}`;
}
export default function DemandHeatmap({ days, selectedDay, onSelectDay }: { days: HeatDay[]; selectedDay: number; onSelectDay: (index: number) => void }) {
  const id = useId().replaceAll(':', '');
  const [minute, setMinute] = useState(0);
  const [fold, setFold] = useState(0);
  const day = days[selectedDay];
  const matches = day?.timeline?.filter(c => c.minute === minute) ?? [];
  const cell = matches.find(c => c.fold === fold) ?? matches[0];
  function inspect(index: number, e: React.PointerEvent<SVGSVGElement>) {
    const rect = e.currentTarget.getBoundingClientRect();
    if (!rect.height) return;
    onSelectDay(index);
    setMinute(Math.max(0, Math.min(1435, Math.floor((1 - (e.clientY - rect.top) / rect.height) * 288) * 5)));
    setFold(e.clientX - rect.left > rect.width / 2 ? 1 : 0);
  }
  return <section className="panel demand-heatmap" aria-label="Daily valve opening heatmap">
    <h3>Valve opening through the day</h3>
    <div className="heat-legend"><span className="heat-scale" aria-hidden="true"/><span>Low opening (blue) → 100% (red)</span><span><i className="heat-closed-key"/>Closed (0%)</span><span><i className="heat-missing-key"/>Missing recordings</span><span><i className="heat-sim-key"/>Simulation excluded</span><span><i className="heat-future-key"/>Not yet elapsed</span></div>
    <p className="history-note-inline">00:00 at the bottom, 23:59 at the top · Europe/Rome · Each strip represents 5 minutes. Color is the time-weighted average opening while active; strip widths distinguish active, closed, and unrecorded time.</p>
    <div className="heat-layout"><div className="heat-axis" aria-hidden="true">{['23:59', '18:00', '12:00', '06:00', '00:00'].map(t => <span key={t}>{t}</span>)}</div>
      <div className="demand-scroll"><div className="heat-days" style={{ minWidth: Math.max(280, days.length * 44) }}>{days.map((d, index) => <div className="heat-day" key={d.date}>
        <svg viewBox="0 0 100 1440" preserveAspectRatio="none" className="heat-column" role="img" aria-label={`${d.date} valve opening from 00:00 bottom to 23:59 top`} onPointerMove={e => inspect(index, e)} onPointerDown={e => inspect(index, e)}>
          <defs><pattern id={`${id}-missing-${index}`} width="10" height="20" patternUnits="userSpaceOnUse"><rect width="10" height="20" className="heat-missing"/><path d="M0 20L10 0" className="heat-hatch"/></pattern><pattern id={`${id}-skipped-${index}`} width="10" height="20" patternUnits="userSpaceOnUse"><rect width="10" height="20" className="heat-skipped"/><path d="M0 0L10 20M0 20L10 0" className="heat-hatch"/></pattern></defs>
          <rect width="100" height="1440" fill={`url(#${id}-skipped-${index})`}/>
          {(d.timeline ?? []).map(c => {
            const width = c.repeated ? 50 : 100, x = c.repeated ? c.fold * 50 : 0, y = 1440 - c.minute - 5;
            const active = Math.max(0, c.active_seconds) / 300 * width;
            const closed = Math.max(0, c.coverage_seconds - c.active_seconds) / 300 * width;
            const simulated = c.simulated_seconds / 300 * width;
            const unknown = c.unknown_seconds / 300 * width;
            return <g key={`${c.minute}-${c.fold}`}><title>{description(d, c)}</title>
              <rect x={x} y={y} width={width} height="5" className="heat-future"/>
              {active > 0 && <rect x={x} y={y} width={active} height="5" fill={openingColor(c.opening_percent ?? 0)}/>}
              {closed > 0 && <rect x={x + active} y={y} width={closed} height="5" className="heat-closed"/>}
              {simulated > 0 && <rect x={x + active + closed} y={y} width={simulated} height="5" className="heat-sim"/>}
              {unknown > 0 && <rect x={x + active + closed + simulated} y={y} width={unknown} height="5" fill={`url(#${id}-missing-${index})`}/>}
            </g>;
          })}
          {[360, 720, 1080].map(y => <line key={y} x1="0" x2="100" y1={y} y2={y} className="heat-grid"/>)}
          {index === selectedDay && <rect x="0" y={1440 - minute - 5} width="100" height="5" className="heat-selection"/>}
        </svg>
        <button className="heat-date" aria-label={`Inspect heatmap ${d.date}`} aria-pressed={index === selectedDay} onClick={() => onSelectDay(index)}>{d.date.slice(5)}</button>
      </div>)}</div></div>
    </div>
    {day && <><label className="history-scrubber">Inspect time<input aria-label="Inspect valve opening time" type="range" min="0" max="1435" step="5" value={minute} onChange={e => setMinute(Number(e.target.value))}/></label>
      {matches.length > 1 && <label className="heat-occurrence">Repeated hour<select aria-label="Repeated hour occurrence" value={cell?.fold ?? 0} onChange={e => setFold(Number(e.target.value))}><option value="0">First occurrence</option><option value="1">Second occurrence</option></select></label>}
      <p className="history-readout" aria-live="polite">{cell ? description(day, cell) : day.timeline ? `${day.date} · ${clock(minute)} · Clock skipped this time (daylight saving).` : 'Timeline data unavailable.'}</p></>}
    <p className="history-note">On daylight-saving days, skipped clock times are crosshatched; a repeated hour is split left (first occurrence) and right (second). Blank areas mean a recorded closed valve, not missing data.</p>
  </section>;
}
