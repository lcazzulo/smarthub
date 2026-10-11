import TemperatureHistory from './TemperatureHistory';
import { useState } from 'react';
import { Bath, BedDouble, CookingPot, Sofa, Thermometer, Minus, Plus, Flame, Radio, ChevronDown } from 'lucide-react';
import { useHeating } from './useHeating';
import { canControl, formatAge, roomStatus } from './model';
import type { Room } from './types';
import type { State } from './model';
import './style.css';
const icons = [Bath, BedDouble, CookingPot, Sofa];
const number = (n: number | null | undefined, suffix = '') => n == null || !Number.isFinite(n) ? '—' : `${n.toFixed(1)}${suffix}`;

function TargetControl({ room, state, submit }: { room: Room; state: State; submit: (room: string, value: number) => void }) {
  const reported = state.snapshot?.reported_target?.temperature_c ?? state.snapshot?.telemetry?.target_temperature_c;
  const [draft, setDraft] = useState<string | null>(null);
  const value = draft ?? (reported == null ? '' : String(reported));
  const command = state.commands[room.id];
  const busy = command?.stage === 'sending' || command?.stage === 'awaiting';
  const enabled = canControl(state, room.enabled) && !busy;
  const numeric = Number(value);
  const valid = value.trim() !== '' && Number.isFinite(numeric) && numeric >= room.target_min_c && numeric <= room.target_max_c
    && Math.abs((numeric - room.target_min_c) / room.target_step_c - Math.round((numeric - room.target_min_c) / room.target_step_c)) < 0.00001;
  function step(direction: number) {
    const base = Number.isFinite(numeric) && value !== '' ? numeric : reported ?? room.target_min_c;
    setDraft(String(Math.max(room.target_min_c, Math.min(room.target_max_c, Math.round((base + direction * room.target_step_c) * 10) / 10))));
  }
  return <section className="target panel">
    <div className="eyebrow">Your comfort</div><h2>Target temperature</h2>
    <form onSubmit={e => { e.preventDefault(); if (enabled && valid) { submit(room.id, numeric); setDraft(null); } }}>
      <div className="target-input">
        <button type="button" className="step" aria-label="Decrease target" disabled={!enabled || numeric <= room.target_min_c} onClick={() => step(-1)}><Minus /></button>
        <label><span className="sr-only">Target temperature in degrees Celsius</span><input type="number" min={room.target_min_c} max={room.target_max_c} step={room.target_step_c} value={value} disabled={!enabled} onChange={e => setDraft(e.target.value)} /><span>°C</span></label>
        <button type="button" className="step" aria-label="Increase target" disabled={!enabled || numeric >= room.target_max_c} onClick={() => step(1)}><Plus /></button>
      </div>
      <p className="muted">Reported target <strong>{number(reported, ' °C')}</strong></p>
      <button className="apply" disabled={!enabled || !valid || numeric === reported}>{busy ? 'Waiting for controller…' : 'Apply target'}</button>
      <p className="hint">{!canControl(state, room.enabled) ? 'Controls unavailable until the room is online with fresh data.' : `Adjust in ${room.target_step_c} °C steps · ${room.target_min_c}–${room.target_max_c} °C`}</p>
    </form>
    {command && <p role="status" className={`command ${command.stage}`}>{command.message}</p>}
  </section>;
}
export default function App() {
  const { rooms, state, now, loadError, retry, selectRoom, setTarget } = useHeating();
  const room = rooms.find(r => r.id === state.selected);
  const t = state.snapshot?.telemetry;
  const status = roomStatus(state);
  const online = state.connection === 'connected' && state.system?.mqtt_connected && state.system.controller_online;
  const age = t?.measurement_age_seconds == null ? null : t.measurement_age_seconds + (state.snapshot?.telemetry_age_seconds ?? 0) + Math.max(0, (now - state.receivedLocally) / 1000);
  return <main>
    <header><div className="brand"><span className="brand-icon"><Flame size={22}/></span><div><h1>Heating</h1><p>Room by room.</p></div></div><span className={`connection ${online ? 'online' : ''}`}><Radio size={15}/>{online ? 'Live connection' : state.connection === 'connecting' ? 'Connecting…' : 'Connection unavailable'}</span></header>
    <div className="intro"><div className="eyebrow">Home climate</div><h2>A little more comfortable.</h2><p>Check your rooms and set the temperature that feels right.</p></div>
    {loadError && <div role="alert" className="panel">{loadError} <button onClick={retry}>Try again</button></div>}
    {!rooms.length && !loadError && <p role="status">Loading rooms…</p>}
    <nav className="rooms" role="tablist" aria-label="Rooms">{rooms.map((r, i) => { const Icon = icons[i] || Thermometer; return <button key={r.id} id={`tab-${r.id}`} role="tab" aria-controls="room-panel" aria-selected={r.id === state.selected} tabIndex={r.id === state.selected ? 0 : -1} onClick={() => selectRoom(r.id)} onKeyDown={e => {
      if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(e.key)) return;
      e.preventDefault(); const index = e.key === 'Home' ? 0 : e.key === 'End' ? rooms.length - 1 : (i + (e.key === 'ArrowRight' ? 1 : -1) + rooms.length) % rooms.length;
      selectRoom(rooms[index].id); document.getElementById(`tab-${rooms[index].id}`)?.focus();
    }}><Icon size={20}/><span>{r.name === 'Livingroom' ? 'Living room' : r.name}</span></button>; })}</nav>
    {room && <div id="room-panel" role="tabpanel" aria-labelledby={`tab-${room.id}`} className={`room-panel tone-${status.tone}`}>
      <div className="room-heading"><h2>{room.name === 'Livingroom' ? 'Living room' : room.name}</h2>{t?.dry_run && <span className="dry-run">Dry run</span>}</div>
      {state.error && <p role="alert">{state.error}</p>}
      <div className="primary-grid"><section className="temperature panel"><div className="eyebrow"><Thermometer size={16}/> Room temperature</div><div className="temperature-value">{number(t?.temperature_c)}<span>°C</span></div><p className="sensor-caption">External room thermometer</p><div className="status"><span className="status-dot"/>{status.label}</div><p className="status-detail">{status.detail}</p></section><TargetControl key={room.id} room={room} state={state} submit={setTarget}/></div>
      <div className="metrics"><section className="panel"><h3>Requested opening</h3><div className="metric-value">{number(t?.opening_percent)}<span>%</span></div><div className="meter" role="meter" aria-label="Requested opening" aria-valuemin={0} aria-valuemax={100} aria-valuenow={t?.opening_percent ?? undefined}><span style={{width: `${Math.max(0, Math.min(100, t?.opening_percent ?? 0))}%`}}/></div><p>{t?.dry_run ? 'Simulated valve demand' : 'Controller demand, not measured position'}</p></section><section className="panel"><h3>Actuator status</h3><div className="metric-text">{t?.actuator_status?.replaceAll('_', ' ') ?? 'Waiting'}</div><p>{t?.fault && t.fault !== 'none' ? `Fault: ${t.fault}` : 'Latest controller report'}</p></section><section className="panel"><h3>Temperature message</h3><div className="metric-text">{formatAge(age)}</div><p>Age of the external sensor reading</p></section></div>
      <TemperatureHistory room={room.id} snapshot={state.snapshot} online={!!online} streamId={state.streamId}/>
      <details className="diagnostics panel" key={`diagnostics-${room.id}`}><summary><span>Controller diagnostics</span><ChevronDown size={18}/></summary><dl><div><dt>Control status</dt><dd>{t?.status ?? '—'}</dd></div><div><dt>P contribution</dt><dd>{number(t?.proportional_percent, '%')}</dd></div><div><dt>I contribution</dt><dd>{number(t?.integral_percent, '%')}</dd></div><div><dt>Actuator fault</dt><dd>{t?.fault ?? '—'}</dd></div><div><dt>Recording</dt><dd>{t?.recording_status ?? '—'}</dd></div><div><dt>Telemetry received</dt><dd>{state.snapshot?.received_at ? new Date(state.snapshot.received_at).toLocaleTimeString() : '—'}</dd></div></dl></details>
      <footer>Updates arrive live from your heating controller.</footer>
    </div>}
  </main>;
}
