import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import ManualMode from './ManualMode';

const rooms = ['bedroom', 'bathroom', 'kitchen', 'living_room'].map(id => ({
  id, name: id, enabled: true, target_min_c: 4, target_max_c: 30, target_step_c: .5,
}));
let state: { available: boolean; state: { token: string; accepted_token: string | null; enabled: boolean; dry_run: boolean;
  openings: Record<string, number>; rooms: Record<string, unknown> } };
let fetchMock: ReturnType<typeof vi.fn>;
beforeEach(() => {
  state = { available: true, state: { token: 'first', accepted_token: null, enabled: false, dry_run: false,
    openings: Object.fromEntries(rooms.map(r => [r.id, 0])),
    rooms: Object.fromEntries(rooms.map(r => [r.id, { status: 'active', requested_percent: 0,
      automatic_percent: 30, reported_percent: 0, actuator_status: 'closed', fault: null }])) } };
  fetchMock = vi.fn().mockImplementation(async (_url, options) => options?.method === 'PUT'
    ? { ok: true, status: 202, json: async () => ({ confirmed: false }) }
    : { ok: true, json: async () => structuredClone(state) });
  vi.stubGlobal('fetch', fetchMock);
});
afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); });

it('shows four bounded sliders disabled until manual mode is acknowledged', async () => {
  render(<ManualMode rooms={rooms}/>);
  const toggle = screen.getByRole('switch', { name: 'Manual mode' });
  await waitFor(() => expect(toggle).toBeEnabled());
  expect(screen.getAllByRole('slider')).toHaveLength(4);
  screen.getAllByRole('slider').forEach(slider => {
    expect(slider).toBeDisabled(); expect(slider).toHaveAttribute('min', '0'); expect(slider).toHaveAttribute('max', '100');
  });
  fireEvent.click(toggle);
  expect(toggle).not.toBeChecked();
  expect(toggle).toBeDisabled();
  const put = fetchMock.mock.calls.find(call => call[1]?.method === 'PUT');
  expect(JSON.parse(put![1].body)).toEqual({ token: 'first', enabled: true, openings: state.state.openings });
  state.state.enabled = true; state.state.accepted_token = 'first'; state.state.token = 'second';
  await waitFor(() => expect(toggle).toBeChecked(), { timeout: 2000 });
  await waitFor(() => expect(toggle).toBeEnabled());
  screen.getAllByRole('slider').forEach(slider => expect(slider).toBeEnabled());
});

it('submits one valve opening while preserving the other requests', async () => {
  state.state.enabled = true;
  state.state.openings.bathroom = 25;
  render(<ManualMode rooms={rooms}/>);
  await waitFor(() => expect(screen.getAllByRole('slider')[0]).toBeEnabled());
  fireEvent.change(screen.getAllByRole('slider')[0], { target: { value: '73' } });
  fireEvent.click(screen.getAllByRole('button', { name: 'Apply opening' })[0]);
  const put = fetchMock.mock.calls.find(call => call[1]?.method === 'PUT');
  expect(JSON.parse(put![1].body).openings).toEqual({ bedroom: 73, bathroom: 25, kitchen: 0, living_room: 0 });
  expect(screen.getByText('Waiting for controller…')).toBeInTheDocument();
});

it('disables controls with stale state and explains startup dry-run', async () => {
  state.available = false; state.state.enabled = true; state.state.dry_run = true;
  render(<ManualMode rooms={rooms}/>);
  await screen.findByText(/Slider requests are simulated/);
  expect(screen.getByRole('switch')).toBeDisabled();
  screen.getAllByRole('slider').forEach(slider => expect(slider).toBeDisabled());
});

it('reports failed submissions without changing the accepted mode', async () => {
  render(<ManualMode rooms={rooms}/>);
  await waitFor(() => expect(screen.getByRole('switch')).toBeEnabled());
  fetchMock.mockResolvedValueOnce({ ok: false, json: async () => ({ error: { message: 'MQTT unavailable' } }) });
  await act(async () => fireEvent.click(screen.getByRole('switch')));
  expect(screen.getByRole('alert')).toHaveTextContent('MQTT unavailable');
  expect(screen.getByRole('switch')).not.toBeChecked();
});

it.each([
  ['waiting_for_temperature', 'Waiting for temperature'],
  ['stale_temperature', 'Temperature reading is stale'],
  ['outside_supply', 'Outside supply hours'],
  ['outside_heating_schedule', 'Outside heating hours'],
  ['disabled', 'Room is disabled'],
])('explains %s and prevents nonzero requests', async (status, reason) => {
  state.state.enabled = true;
  Object.assign(state.state.rooms.bedroom!, { status });
  render(<ManualMode rooms={rooms}/>);
  await screen.findByText(`Blocked: ${reason}. Opening requests cannot run. Once ready, apply a new request.`);
  fireEvent.change(screen.getAllByRole('slider')[0], { target: { value: '75' } });
  expect(screen.getAllByRole('button', { name: 'Apply opening' })[0]).toBeDisabled();
  expect(fetchMock.mock.calls.some(call => call[1]?.method === 'PUT')).toBe(false);
});

it('allows a zero request even when a fault blocks opening', async () => {
  state.state.enabled = true; state.state.openings.bedroom = 50;
  Object.assign(state.state.rooms.bedroom!, { fault: 'No matching report' });
  render(<ManualMode rooms={rooms}/>);
  await screen.findByText(/Blocked: Valve fault: No matching report/);
  fireEvent.change(screen.getAllByRole('slider')[0], { target: { value: '0' } });
  const button = screen.getAllByRole('button', { name: 'Apply opening' })[0];
  expect(button).toBeEnabled();
  fireEvent.click(button);
  expect(JSON.parse(fetchMock.mock.calls.find(call => call[1]?.method === 'PUT')![1].body).openings.bedroom).toBe(0);
});

it('shows a blocked state arriving after acknowledgement without claiming success', async () => {
  state.state.enabled = true;
  render(<ManualMode rooms={rooms}/>);
  await waitFor(() => expect(screen.getAllByRole('slider')[0]).toBeEnabled());
  fireEvent.change(screen.getAllByRole('slider')[0], { target: { value: '75' } });
  fireEvent.click(screen.getAllByRole('button', { name: 'Apply opening' })[0]);
  state.state.accepted_token = 'first'; state.state.token = 'second';
  Object.assign(state.state.rooms.bedroom!, { status: 'waiting_for_temperature' });
  await screen.findByText(/Blocked: Waiting for temperature/, {}, { timeout: 2000 });
  expect(screen.queryByText(/Controller accepted the request/)).not.toBeInTheDocument();
  expect(screen.getByText(/Request received by controller/)).toHaveTextContent('does not confirm valve movement');
  expect(screen.getAllByRole('slider')[0]).toHaveValue('0');
});

it('distinguishes waiting for an update from a matching reported setting', async () => {
  state.state.enabled = true; state.state.openings.bedroom = 25;
  Object.assign(state.state.rooms.bedroom!, { requested_percent: 25, reported_percent: 100, actuator_status: 'active' });
  render(<ManualMode rooms={rooms}/>);
  await screen.findByText(/Waiting for valve update/);
  expect(screen.getByText(/Waiting for valve update/)).toHaveTextContent('configured command interval');
  Object.assign(state.state.rooms.bedroom!, { reported_percent: 25 });
  await waitFor(() => expect(screen.queryByText(/Waiting for valve update/)).not.toBeInTheDocument(), { timeout: 2000 });
});

it.each([true, false])('accepts manual status and submits opening with room enabled=%s', async (enabled) => {
  state.state.enabled = true;
  Object.assign(state.state.rooms.bedroom!, { status: 'manual' });
  render(<ManualMode rooms={rooms.map(room => room.id === 'bedroom' ? { ...room, enabled } : room)}/>);
  await waitFor(() => expect(screen.getAllByRole('slider')[0]).toBeEnabled());
  fireEvent.change(screen.getAllByRole('slider')[0], { target: { value: '75' } });
  const button = screen.getAllByRole('button', { name: 'Apply opening' })[0];
  expect(button).toBeEnabled();
  expect(screen.queryByText(/Blocked:/)).not.toBeInTheDocument();
  fireEvent.click(button);
  expect(JSON.parse(fetchMock.mock.calls.find(call => call[1]?.method === 'PUT')![1].body).openings.bedroom).toBe(75);
});

it('retains fault checks with manual status', async () => {
  state.state.enabled = true;
  Object.assign(state.state.rooms.bedroom!, { status: 'manual', fault: 'No matching report' });
  render(<ManualMode rooms={rooms}/>);
  await screen.findByText(/Blocked: Valve fault/);
  fireEvent.change(screen.getAllByRole('slider')[0], { target: { value: '75' } });
  expect(screen.getAllByRole('button', { name: 'Apply opening' })[0]).toBeDisabled();
});
