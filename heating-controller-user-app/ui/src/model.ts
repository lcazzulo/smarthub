import type { Command, Snapshot, StreamEvent, SystemState, TargetReport } from './types';

export interface State {
  connection: 'connecting' | 'connected' | 'disconnected';
  system: SystemState | null;
  snapshot: Snapshot | null;
  selected: string;
  streamId: string | null;
  sequence: number;
  receivedLocally: number;
  commands: Record<string, Command>;
  error: string | null;
}
export const initialState: State = {
  connection: 'connecting', system: null, snapshot: null, selected: '',
  streamId: null, sequence: -1, receivedLocally: 0, commands: {}, error: null,
};
export type Action =
  | { type: 'select'; room: string }
  | { type: 'connection'; value: State['connection'] }
  | { type: 'event'; event: StreamEvent; now: number }
  | { type: 'request'; room: string; value: number; now: number }
  | { type: 'submitted'; room: string; startedAt: number }
  | { type: 'failed'; room: string; startedAt: number; message: string }
  | { type: 'tick'; now: number };

function uncertain(commands: State['commands']) {
  return Object.fromEntries(Object.entries(commands).map(([room, command]) => [room,
    ['sending', 'awaiting'].includes(command.stage)
      ? { ...command, stage: 'unconfirmed' as const, message: 'Connection changed. Check the reported target before trying again.' }
      : command]));
}
export function reducer(state: State, action: Action): State {
  switch (action.type) {
    case 'select': return { ...state, selected: action.room, snapshot: null, error: null };
    case 'connection': return { ...state, connection: action.value,
      system: action.value === 'connected' ? state.system : null,
      snapshot: action.value === 'connected' ? state.snapshot : null,
      // Sequence values can repeat on a new backend process.
      streamId: action.value === 'connected' ? state.streamId : null,
      sequence: action.value === 'connected' ? state.sequence : -1,
      commands: action.value === 'connected' ? state.commands : uncertain(state.commands) };
    case 'event': {
      const e = action.event;
      if (e.version !== 1 || !Number.isSafeInteger(e.sequence) || typeof e.stream_id !== 'string') return state;
      if (e.stream_id === state.streamId && e.sequence <= state.sequence) return state;
      let next = { ...state, sequence: e.sequence, streamId: e.stream_id };
      if (state.streamId && e.stream_id !== state.streamId) next = { ...next, snapshot: null, commands: uncertain(state.commands) };
      if (e.type === 'system.state') {
        const system = e.data as SystemState;
        if (typeof system?.mqtt_connected !== 'boolean' || typeof system?.controller_online !== 'boolean') return state;
        return { ...next, system,
          commands: system.mqtt_connected && system.controller_online ? next.commands : uncertain(next.commands) };
      }
      if (e.type === 'error') return { ...next, error: (e.data as { message?: string })?.message || 'Subscription failed' };
      if (e.room_id !== state.selected) return next;
      if (e.type === 'room.state') {
        const snapshot = e.data as Snapshot;
        if (snapshot?.room_id !== state.selected || typeof snapshot.available !== 'boolean') return state;
        return { ...next, snapshot, receivedLocally: action.now, error: null };
      }
      if (e.type === 'room.target') {
        const target = e.data as TargetReport;
        if (!Number.isFinite(target?.temperature_c)) return state;
        next.snapshot = next.snapshot ? { ...next.snapshot, reported_target: target } : null;
        const pending = next.commands[state.selected];
        if (pending && ['sending', 'awaiting', 'unconfirmed'].includes(pending.stage)
          && pending.streamId === e.stream_id && e.sequence > pending.afterSequence && !target.retained
          && Math.abs(target.temperature_c - pending.value) < 0.001) {
          next.commands = { ...next.commands, [state.selected]: { ...pending, stage: 'reported', message: 'Target reported by controller.' } };
        }
      }
      return next;
    }
    case 'request': return { ...state, commands: { ...state.commands, [action.room]: {
      value: action.value, stage: 'sending', startedAt: action.now, afterSequence: state.sequence,
      streamId: state.streamId, message: 'Sending target…',
    } } };
    case 'submitted': {
      const command = state.commands[action.room];
      if (!command || command.startedAt !== action.startedAt || command.stage !== 'sending') return state;
      return { ...state, commands: { ...state.commands, [action.room]: { ...command, stage: 'awaiting', message: 'Waiting for the controller to report this target…' } } };
    }
    case 'failed': {
      const command = state.commands[action.room];
      if (!command || command.startedAt !== action.startedAt || command.stage === 'reported') return state;
      return { ...state, commands: { ...state.commands, [action.room]: { ...command, stage: 'error', message: action.message } } };
    }
    case 'tick': {
      const commands = Object.fromEntries(Object.entries(state.commands).map(([room, c]) => [room,
        ['sending', 'awaiting'].includes(c.stage) && action.now - c.startedAt > 20000
          ? { ...c, stage: 'unconfirmed' as const, message: 'No target confirmation yet. Check the reported target before retrying.' } : c]));
      return { ...state, commands };
    }
  }
}

export function canControl(state: State, enabled: boolean): boolean {
  return enabled && state.connection === 'connected' && !!state.system?.mqtt_connected
    && !!state.system.controller_online && !!state.snapshot?.available && !state.snapshot.stale && !state.error;
}

export function roomStatus(state: State): { label: string; tone: string; detail: string } {
  const t = state.snapshot?.telemetry;
  if (!state.snapshot || !canControl(state, true)) return { label: 'Waiting for live data', tone: 'neutral', detail: 'Controls will be available when a fresh room update arrives.' };
  if (!t) return { label: 'Waiting for temperature', tone: 'purple', detail: 'The controller has not reported a room temperature yet.' };
  if (t.fault && t.fault !== 'none') return { label: 'Actuator fault', tone: 'red', detail: t.fault };
  const statuses: Record<string, [string, string, string]> = {
    disabled: ['Room disabled', 'neutral', 'Heating is disabled for this room in the controller configuration.'],
    waiting_for_temperature: ['Waiting for temperature', 'purple', 'Waiting for a fresh reading from the external room thermometer.'],
    stale_temperature: ['Temperature is stale', 'purple', 'The controller has inhibited heating because temperature messages are too old.'],
    outside_supply: ['Outside supply hours', 'slate', 'The shared heating supply is outside its scheduled hours. The controller requests closure.'],
    outside_heating_schedule: ['Outside heating schedule', 'blue', 'The room is outside the desired heating hours. The controller requests closure.'],
  };
  if (statuses[t.status]) {
    const [label, tone, detail] = statuses[t.status]; return { label, tone, detail };
  }
  if (t.dry_run) return { label: 'Simulating control', tone: 'amber', detail: 'Demand and actuator state are simulated. No device commands are sent.' };
  if (t.hvac_action === 'heating') return { label: 'Heating requested', tone: 'orange', detail: 'The controller is requesting heat. Reported settings do not measure water flow.' };
  if (t.opening_percent > 0) return { label: 'Preparing to heat', tone: 'neutral', detail: 'The controller is waiting for the valve setup or opening to be acknowledged.' };
  return { label: 'No heat requested', tone: 'neutral', detail: 'The current control calculation requests no valve opening.' };
}
export function formatAge(seconds: number | null): string {
  if (seconds === null || !Number.isFinite(seconds)) return 'Not received';
  if (seconds < 60) return `${Math.floor(Math.max(0, seconds))}s ago`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ${Math.floor(seconds % 60)}s ago`;
  return `${Math.floor(seconds / 3600)}h ${Math.floor(seconds % 3600 / 60)}m ago`;
}
