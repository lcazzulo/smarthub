import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import TemperatureHistory from './TemperatureHistory';
afterEach(()=>vi.unstubAllGlobals());
const page={samples:[{at:new Date(Date.now()-30000).toISOString(),temperature_c:23,target_temperature_c:20,break_before:true,control_status:'active'}],next_cursor:null};
it('loads ranges on demand, preserves range when changing rooms and pauses live data',async()=>{
  const fetch=vi.fn().mockResolvedValue({ok:true,json:async()=>page});vi.stubGlobal('fetch',fetch);
  const {rerender}=render(<TemperatureHistory room="bathroom" snapshot={null} online={true} streamId="a"/>);
  await screen.findByRole('img',{name:'Temperature and target in degrees Celsius over time'});expect(fetch).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByRole('button',{name:'6h'}));await waitFor(()=>expect(fetch).toHaveBeenCalledTimes(2));await screen.findByRole('img',{name:'Temperature and target in degrees Celsius over time'});
  rerender(<TemperatureHistory room="bedroom" snapshot={null} online={true} streamId="a"/>);
  await waitFor(()=>expect(fetch).toHaveBeenCalledTimes(3));await screen.findByRole('img',{name:'Temperature and target in degrees Celsius over time'});
  expect(screen.getByRole('button',{name:'6h'})).toHaveAttribute('aria-pressed','true');
  expect(fetch.mock.calls[2][0]).toContain('/bedroom/history');
  fireEvent.click(screen.getByRole('checkbox',{name:'Follow live'}));
  expect(screen.getByText(/Live updates paused/)).toBeInTheDocument();
  const snapshot={room_id:'bedroom',available:true,stale:false,received_at:new Date(Date.now()+1000).toISOString(),telemetry_age_seconds:0,reported_target:null,telemetry:{temperature_c:25,target_temperature_c:21,status:'active',opening_percent:0,measurement_age_seconds:0,proportional_percent:0,integral_percent:0,actuator_status:'ready',fault:'none',dry_run:false,hvac_action:'idle'}};
  rerender(<TemperatureHistory room="bedroom" snapshot={snapshot} online={true} streamId="a"/>);
  expect(screen.getByRole('slider')).toHaveAttribute('max','0');expect(fetch).toHaveBeenCalledTimes(3);
  fireEvent.click(screen.getByRole('checkbox',{name:'Follow live'}));
  await waitFor(()=>expect(fetch).toHaveBeenCalledTimes(4));await screen.findByRole('img',{name:'Temperature and target in degrees Celsius over time'});
});
it('shows an actionable error and retries',async()=>{
  vi.stubGlobal('fetch',vi.fn().mockResolvedValueOnce({ok:false}).mockResolvedValueOnce({ok:true,json:async()=>page}));
  render(<TemperatureHistory room="bathroom" snapshot={null} online={false} streamId={null}/>);
  await screen.findByRole('alert');fireEvent.click(screen.getByRole('button',{name:'Retry'}));await screen.findByRole('img',{name:'Temperature and target in degrees Celsius over time'});
});
it('appends live telemetry without another HTTP request',async()=>{
  const fetch=vi.fn().mockResolvedValue({ok:true,json:async()=>page});vi.stubGlobal('fetch',fetch);
  const {rerender}=render(<TemperatureHistory room="bathroom" snapshot={null} online={true} streamId="a"/>);
  await screen.findByRole('img',{name:'Temperature and target in degrees Celsius over time'});
  const snapshot={room_id:'bathroom',available:true,stale:false,received_at:new Date(Date.now()+1000).toISOString(),telemetry_age_seconds:0,reported_target:null,telemetry:{temperature_c:25,target_temperature_c:21,status:'active',opening_percent:0,measurement_age_seconds:0,proportional_percent:0,integral_percent:0,actuator_status:'ready',fault:'none',dry_run:false,hvac_action:'idle'}};
  rerender(<TemperatureHistory room="bathroom" snapshot={snapshot} online={true} streamId="a"/>);
  expect(screen.getByRole('slider')).toHaveAttribute('max','1');expect(fetch).toHaveBeenCalledTimes(1);
  fireEvent.change(screen.getByRole('slider'),{target:{value:'0'}});
  fireEvent.change(screen.getByRole('slider'),{target:{value:'1'}});
  expect(screen.getByText(/Temperature 25.0 °C · Target 21.0 °C/)).toBeInTheDocument();
});
