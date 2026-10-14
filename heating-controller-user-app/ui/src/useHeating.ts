import { useEffect, useReducer, useRef, useState } from 'react';
import { initialState, reducer } from './model';
import type { RecordedReadings, Room, StreamEvent } from './types';

export function useHeating() {
  const [rooms, setRooms] = useState<Room[]>([]);
  const [recordedReadings, setRecordedReadings] = useState<Record<string, RecordedReadings>>({});
  const [loadError, setLoadError] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  const [state, dispatch] = useReducer(reducer, initialState);
  const [now, setNow] = useState(Date.now());
  const selected = useRef('');
  const socket = useRef<WebSocket | null>(null);
  const current = useRef(state);
  current.current = state;
  const requests = useRef(new Set<AbortController>());

  useEffect(() => {
    const abort = new AbortController();
    setLoadError(null);
    fetch('/api/v1/rooms', { signal: abort.signal })
      .then(async response => {
        if (!response.ok) throw new Error('The backend could not load the rooms.');
        const data = await response.json();
        if (!Array.isArray(data.rooms) || !data.rooms.length) throw new Error('No rooms are configured.');
        const order = ['bathroom', 'bedroom', 'kitchen', 'living_room'];
        const list: Room[] = [...data.rooms].sort((a, b) => order.indexOf(a.id) - order.indexOf(b.id));
        setRooms(list);
        const hash = window.location.hash.slice(1);
        const id = list.find(r => r.id === hash)?.id || list[0].id;
        selected.current = id;
        dispatch({ type: 'select', room: id });
      })
      .catch(error => { if (!abort.signal.aborted) setLoadError(error.message || 'Unable to load rooms.'); });
    return () => abort.abort();
  }, [reload]);

  const ready = rooms.length > 0;

  useEffect(() => {
    if (!state.selected) return;
    const abort = new AbortController();
    fetch(`/api/v1/rooms/${encodeURIComponent(state.selected)}/readings`, { signal: abort.signal })
      .then(async response => {
        if (!response.ok) throw new Error('Recorded sensor readings are unavailable.');
        const data = await response.json() as RecordedReadings;
        if (!abort.signal.aborted) setRecordedReadings(current => ({ ...current, [state.selected]: data }));
      })
      .catch(() => { /* Live telemetry remains available when recording is disabled. */ });
    return () => abort.abort();
  }, [state.selected]);
  useEffect(() => {
    if (!ready) return;
    let stopped = false;
    let retry = 0;
    let reconnect: ReturnType<typeof setTimeout>;
    let connection: WebSocket;
    function connect() {
      if (stopped) return;
      dispatch({ type: 'connection', value: 'connecting' });
      const scheme = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
      connection = new WebSocket(`${scheme}//${window.location.host}/ws`);
      socket.current = connection;
      connection.onopen = () => {
        retry = 0;
        dispatch({ type: 'connection', value: 'connected' });
        connection.send(JSON.stringify({ type: 'subscribe', room_id: selected.current }));
      };
      connection.onmessage = ({ data }) => {
        if (stopped) return;
        try {
          const event = JSON.parse(data) as StreamEvent;
          if (event && typeof event === 'object') dispatch({ type: 'event', event, now: Date.now() });
        } catch { /* Ignore invalid frames; cached values never gain freshness. */ }
      };
      connection.onerror = () => connection.close();
      connection.onclose = () => {
        if (stopped) return;
        dispatch({ type: 'connection', value: 'disconnected' });
        reconnect = setTimeout(connect, Math.min(1000 * 2 ** retry++, 15000));
      };
    }
    connect();
    return () => { stopped = true; clearTimeout(reconnect); connection?.close(); socket.current = null; };
  }, [ready]);

  useEffect(() => {
    // Local labels and pending-command timeout only. No network polling.
    const timer = setInterval(() => { const now = Date.now(); setNow(now); dispatch({ type: 'tick', now }); }, 1000);
    return () => { clearInterval(timer); for (const request of requests.current) request.abort(); };
  }, []);

  function selectRoom(id: string) {
    if (id === selected.current) return;
    selected.current = id;
    window.history.replaceState(null, '', `#${id}`);
    dispatch({ type: 'select', room: id });
    if (socket.current?.readyState === WebSocket.OPEN) {
      socket.current.send(JSON.stringify({ type: 'subscribe', room_id: id }));
    }
  }

  async function setTarget(room: string, value: number) {
    const now = Date.now();
    const pending = current.current.commands[room];
    if (pending && ['sending', 'awaiting'].includes(pending.stage)) return;
    dispatch({ type: 'request', room, value, now });
    const abort = new AbortController();
    requests.current.add(abort);
    const timeout = setTimeout(() => abort.abort(), 15000);
    try {
      const response = await fetch(`/api/v1/rooms/${encodeURIComponent(room)}/target`, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ temperature_c: value }), signal: abort.signal,
      });
      const data = await response.json();
      if (response.status !== 202) throw new Error(data.error?.message || 'The controller did not accept the request.');
      dispatch({ type: 'submitted', room, startedAt: now });
    } catch (error) {
      dispatch({ type: 'failed', room, startedAt: now, message: error instanceof Error && error.name !== 'AbortError'
        ? error.message : 'Request outcome is unknown. Check the reported target before retrying.' });
    } finally { clearTimeout(timeout); requests.current.delete(abort); }
  }

  return { rooms, state, recordedReadings: recordedReadings[state.selected], now, loadError, retry: () => setReload(v => v + 1), selectRoom, setTarget };
}
