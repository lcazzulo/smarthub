import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';
import { beforeEach, afterEach, expect, it, vi } from 'vitest';
import App from './App';
vi.mock('./TemperatureHistory',()=>({default:()=>null}));
class Socket {
  static OPEN=1; static instances: Socket[]=[]; readyState=1;
  onopen: (()=>void)|null=null; onmessage: ((event:{data:string})=>void)|null=null; onclose:(()=>void)|null=null; onerror:(()=>void)|null=null;
  send=vi.fn(); close=vi.fn();
  constructor(){Socket.instances.push(this)}
}
let fetchMock: ReturnType<typeof vi.fn>;
beforeEach(()=>{
  Socket.instances=[]; window.history.replaceState(null,'','/#');
  vi.stubGlobal('WebSocket',Socket);
  fetchMock=vi.fn().mockResolvedValue({ok:true,json:async()=>({rooms:['bathroom','bedroom','kitchen','living_room'].map(id=>({id,name:id,enabled:true,target_min_c:4,target_max_c:30,target_step_c:.5}))})});
  vi.stubGlobal('fetch',fetchMock);
});
afterEach(()=>vi.unstubAllGlobals());
it('subscribes, displays data, sends a target and switches subscriptions without polling',async()=>{
  render(<App/>);
  await screen.findByRole('tab',{name:'bathroom'});
  await waitFor(()=>expect(Socket.instances).toHaveLength(1));
  const socket=Socket.instances[0];
  const push=(type:string,sequence:number,data:unknown)=>socket.onmessage?.({data:JSON.stringify({version:1,type,stream_id:'s',sequence,room_id:'bathroom',data})});
  act(()=>{
    socket.onopen?.();
    push('system.state',1,{mqtt_connected:true,controller_online:true});
    push('room.state',2,{room_id:'bathroom',available:true,stale:false,telemetry_age_seconds:0,telemetry:{temperature_c:23,target_temperature_c:20,opening_percent:0,status:'active',measurement_age_seconds:5,proportional_percent:0,integral_percent:0,actuator_status:'ready',fault:'none',dry_run:false,hvac_action:'idle'},reported_target:null});
  });
  expect(screen.getByText('23.0')).toBeInTheDocument();
  expect(socket.send).toHaveBeenCalledWith(JSON.stringify({type:'subscribe',room_id:'bathroom'}));
  fireEvent.click(screen.getByRole('button',{name:'Increase target'}));
  fetchMock.mockResolvedValueOnce({status:202,json:async()=>({status:'submitted'})});
  fireEvent.click(screen.getByRole('button',{name:'Apply target'}));
  await screen.findByText('Waiting for the controller to report this target…');
  expect(fetchMock).toHaveBeenLastCalledWith('/api/v1/rooms/bathroom/target',expect.objectContaining({method:'PUT',body:'{"temperature_c":20.5}'}));
  act(()=>push('room.target',3,{temperature_c:20.5,retained:false,received_at:''}));
  expect(screen.getByText('Target reported by controller.')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('tab',{name:'bedroom'}));
  expect(socket.send).toHaveBeenLastCalledWith(JSON.stringify({type:'subscribe',room_id:'bedroom'}));
  expect(screen.getByRole('button',{name:'Apply target'})).toBeDisabled();
  expect(screen.queryByText('23.0')).not.toBeInTheDocument();
  expect(fetchMock.mock.calls.map(call => call[0])).toEqual([
    '/api/v1/rooms', '/api/v1/rooms/bathroom/readings',
    '/api/v1/rooms/bathroom/target', '/api/v1/rooms/bedroom/readings',
  ]);
});

it('opens humidity and outside temperature history from their values', async () => {
  HTMLDialogElement.prototype.showModal = function () { this.setAttribute('open', ''); };
  render(<App/>);
  await screen.findByRole('tab', { name: 'bathroom' });
  await waitFor(() => expect(Socket.instances).toHaveLength(1));
  const socket = Socket.instances[0];
  act(() => {
    socket.onopen?.();
    socket.onmessage?.({ data: JSON.stringify({ version: 1, type: 'room.state', stream_id: 's', sequence: 1, room_id: 'bathroom', data: { room_id: 'bathroom', available: true, stale: false, telemetry: { temperature_c: 23, humidity_percent: 47 } } }) });
  });
  fetchMock.mockResolvedValue({ ok: true, json: async () => ({ samples: [], next_cursor: null }) });
  const humidity = screen.getByRole('button', { name: 'Show humidity history' });
  humidity.focus();
  fireEvent.click(humidity);
  expect(screen.getByRole('dialog', { name: 'bathroom humidity history' })).toBeInTheDocument();
  await screen.findByText('No recorded samples in this time range.');
  expect(fetchMock.mock.calls.at(-1)?.[0]).toContain('series=humidity');
  fireEvent.click(screen.getByRole('button', { name: 'Close history' }));
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(humidity).toHaveFocus();
  fireEvent.click(screen.getByRole('button', { name: 'Show outside temperature history' }));
  expect(screen.getByRole('dialog', { name: 'Outside temperature history' })).toBeInTheDocument();
  await screen.findByText('No recorded samples in this time range.');
  expect(fetchMock.mock.calls.at(-1)?.[0]).toContain('/api/v1/outdoor/history?');
});

it('navigates to History and back while keeping the live socket', async () => {
  render(<App/>);
  await screen.findByRole('tab', { name: 'bathroom' });
  await waitFor(() => expect(Socket.instances).toHaveLength(1));
  fetchMock.mockResolvedValue({ ok: true, json: async () => ({ samples: [], next_cursor: null }) });
  fireEvent.click(screen.getByRole('link', { name: 'History' }));
  expect(window.location.pathname).toBe('/history');
  expect(screen.queryByRole('tablist')).not.toBeInTheDocument();
  await screen.findByLabelText('Room');
  await waitFor(() => expect(screen.getAllByText('No recorded samples in this time range.')).toHaveLength(2));
  fireEvent.click(screen.getByRole('link', { name: 'Overview' }));
  expect(window.location.pathname).toBe('/');
  expect(screen.getByRole('tab', { name: 'bathroom' })).toBeInTheDocument();
  expect(Socket.instances).toHaveLength(1);
});

it('opens Heating demand and supports browser navigation', async () => {
  render(<App/>);
  await screen.findByRole('tab', { name: 'bathroom' });
  fetchMock.mockResolvedValue({ ok: true, json: async () => ({ days: [] }) });
  fireEvent.click(screen.getByRole('link', { name: 'Heating demand' }));
  expect(window.location.pathname).toBe('/demand');
  expect(screen.getByRole('link', { name: 'Heating demand' })).toHaveAttribute('aria-current', 'page');
  await screen.findByText('No non-simulated recording coverage in this range.');
  act(() => { window.history.replaceState(null, '', '/'); window.dispatchEvent(new PopStateEvent('popstate')); });
  expect(screen.getByRole('tab', { name: 'bathroom' })).toBeInTheDocument();
  expect(screen.queryByLabelText('Heating demand filters')).not.toBeInTheDocument();
});
