import { useEffect, useRef, useState } from 'react';
import type { Room } from './types';
import './manual.css';

interface ManualState {
  token: string;
  accepted_token: string | null;
  enabled: boolean;
  dry_run: boolean;
  openings: Record<string, number>;
  rooms: Record<string, { status: string; requested_percent: number; automatic_percent: number | null;
    reported_percent: number | null; actuator_status: string; fault: string | null }>;
}
interface Snapshot { available: boolean; state: ManualState | null }
const percent = (value: number | null | undefined) => value == null ? '—' : `${Math.round(value)}%`;

type ValveState = ManualState['rooms'][string];
function blockedReason(detail: ValveState | undefined, enabled: boolean): string | null {
  if (!detail) return 'Waiting for controller data';
  if (detail.fault) return `Valve fault: ${detail.fault}`;
  if (detail.status === 'manual') return null;
  if (!enabled) return 'Room is disabled';
  const reasons: Record<string, string> = {
    waiting_for_temperature: 'Waiting for temperature',
    stale_temperature: 'Temperature reading is stale',
    outside_supply: 'Outside supply hours',
    outside_heating_schedule: 'Outside heating hours',
    disabled: 'Room is disabled',
  };
  return detail.status === 'active' ? null : reasons[detail.status] ?? 'Room is not ready';
}
function valveMessage(state: ManualState, detail: ValveState | undefined, room: Room): string {
  if (!state.enabled) return 'Automatic control is active.';
  const blocked = blockedReason(detail, room.enabled);
  if (blocked) return `Blocked: ${blocked}. Opening requests cannot run. Once ready, apply a new request.`;
  if (state.dry_run) return 'Simulated request only. No valve commands are sent.';
  const requested = state.openings[room.id] ?? 0;
  if (requested !== detail!.requested_percent) return 'Waiting for controller evaluation. The valve has not yet been commanded to this setting.';
  if (detail!.reported_percent === requested &&
      detail!.actuator_status === (requested === 0 ? 'closed' : 'active')) {
    return 'Reported setting matches the request. This does not confirm physical valve position.';
  }
  if (requested === 0) return 'Closure requested. Waiting for the valve to report closed settings.';
  if (detail!.actuator_status === 'closed' || detail!.actuator_status === 'unknown') {
    return 'Preparing valve. Waiting for closure and setup checks before opening.';
  }
  return 'Waiting for valve update. Opening changes may wait for the configured command interval and valve acknowledgement.';
}

export default function ManualMode({ rooms }: { rooms: Room[] }) {
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [drafts, setDrafts] = useState<Record<string, number>>({});
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const [connectionError, setConnectionError] = useState('');
  const pending = useRef<{ token: string; deadline: number } | null>(null);
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    const abort = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function refresh() {
      try {
        const response = await fetch('/api/v1/manual', { signal: AbortSignal.any([abort.signal, AbortSignal.timeout(3000)]), cache: 'no-store' });
        if (!response.ok) throw new Error('Manual controls are unavailable.');
        const next: Snapshot = await response.json();
        if (abort.signal.aborted) return;
        setSnapshot(next);
        setConnectionError('');
        if (pending.current) {
          if (next.available && next.state?.accepted_token === pending.current.token) {
            setMessage('Request received by controller. Check each valve’s status below; this does not confirm valve movement.');
            setDrafts({}); setBusy(false); pending.current = null;
          } else if (Date.now() > pending.current.deadline) {
            setError('No controller confirmation. Refresh the settings before trying again.');
            setBusy(false); pending.current = null;
          }
        }
      } catch {
        if (!abort.signal.aborted) {
          setSnapshot(null);
          setConnectionError('Connection unavailable. Manual controls are disabled.');
          if (pending.current && Date.now() > pending.current.deadline) {
            setBusy(false); pending.current = null;
          }
        }
      } finally {
        if (!abort.signal.aborted) timer = setTimeout(refresh, 1000);
      }
    }
    void refresh();
    return () => { mounted.current = false; abort.abort(); clearTimeout(timer); };
  }, []);
  const state = snapshot?.state;
  const available = !!snapshot?.available && !!state;
  async function submit(enabled: boolean, openings: Record<string, number>) {
    if (!state || !available || busy) return;
    setBusy(true); setError(''); setMessage('Waiting for controller…');
    pending.current = { token: state.token, deadline: Date.now() + 10000 };
    try {
      const response = await fetch('/api/v1/manual', {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ token: state.token, enabled, openings }),
        signal: AbortSignal.timeout(10000),
      });
      if (!response.ok) {
        const result = await response.json();
        throw new Error(result.error?.message || 'Could not submit the manual request.');
      }
    } catch (reason) {
      if (mounted.current) {
        setError(reason instanceof Error ? reason.message : 'Request failed.');
        setMessage(''); setBusy(false); pending.current = null;
      }
    }
  }
  return <div className="manual-page">
    <div className="intro"><div className="eyebrow">Valve controls</div><h2>Manual mode</h2>
      <p>Pause automatic valve control and choose each valve’s opening.</p></div>
    <section className="panel manual-mode-switch">
      <div><h3>Manual mode</h3><p>{state?.enabled ? 'Automatic demand is previewed only. Sliders control the valves.' : 'The controller follows automatic demand.'}</p></div>
      <label className="manual-toggle"><input type="checkbox" role="switch" aria-label="Manual mode"
        checked={state?.enabled ?? false} disabled={!available || busy}
        onChange={e => { setDrafts({}); void submit(e.target.checked, Object.fromEntries(Object.keys(state!.openings).map(key => [key, 0]))); }}/>
        <span>{state?.enabled ? 'On' : 'Off'}</span></label>
    </section>
    {!available && <p role="status">Waiting for fresh controller state. Controls are unavailable.</p>}
    {state?.dry_run && <p className="panel">The controller was started in dry-run mode. Slider requests are simulated; no valve commands are sent.</p>}
    <p className="muted">Enabling manual mode requests closure first. Manual mode overrides supply hours, heating hours, room enablement and temperature checks. Valve communication checks still apply. Turning it off resumes automatic control. Opening adjustments are rate-limited and require valve reports; a zero request bypasses the opening-change interval.</p>
    {connectionError && <p role="alert">{connectionError}</p>}{error && <p role="alert">{error}</p>}{message && <p role="status">{message}</p>}
    <div className="manual-grid">{rooms.map(room => {
      const detail = state?.rooms[room.id];
      const value = drafts[room.id] ?? state?.openings[room.id] ?? 0;
      const enabled = available && !!state?.enabled && !!detail && !busy;
      const blocked = blockedReason(detail, room.enabled);
      const canApply = enabled && (value === 0 || !blocked);
      return <section className="panel manual-valve" key={room.id}>
        <h3>{room.name === 'Livingroom' ? 'Living room' : room.name}</h3>
        <label htmlFor={`opening-${room.id}`}>Choose opening <strong>{value}%</strong></label>
        <input id={`opening-${room.id}`} type="range" min="0" max="100" step="1" value={value}
          disabled={!enabled} aria-describedby={`manual-status-${room.id}`} onChange={e => setDrafts(previous => ({ ...previous, [room.id]: Number(e.target.value) }))}/>
        <div className="manual-scale"><span>0% · Closed</span><span>100% · Open</span></div>
        <button className="apply" disabled={!canApply || value === state?.openings[room.id]}
          onClick={() => { if (canApply) void submit(true, { ...state!.openings, [room.id]: value }); }}>Apply opening</button>
        <p id={`manual-status-${room.id}`} role="status">{available && state
          ? valveMessage(state, detail, room) : 'Status unavailable. Waiting for fresh controller data.'}</p>
        <dl><div><dt>Controller request</dt><dd>{percent(state?.openings[room.id])}</dd></div>
          <div><dt>Reported setting</dt><dd>{percent(detail?.reported_percent)}</dd></div>
          <div><dt>Automatic preview</dt><dd>{percent(detail?.automatic_percent)}</dd></div>
          <div><dt>Controller status</dt><dd>{detail?.status.replaceAll('_', ' ') ?? 'Waiting'}</dd></div>
          <div><dt>Actuator</dt><dd>{detail?.actuator_status.replaceAll('_', ' ') ?? 'Waiting'}</dd></div></dl>

      </section>;
    })}</div>
    <p className="muted">Reported settings do not measure physical valve position. Manual mode stays enabled if this page is closed; a broker reconnect clears requested openings. Restarting the controller restores automatic mode.</p>
  </div>;
}
