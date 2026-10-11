import { render, screen } from '@testing-library/react';
import { expect, it } from 'vitest';
import ValveDemandChart from './ValveDemandChart';
import { historyPoint, livePoint, statusBands } from './history';
it('maps demand and supply independently of measured temperature',()=>{
 const point=historyPoint({at:'2026-10-11T12:00:00Z',temperature_c:23,target_temperature_c:20,break_before:true,control_status:'outside_supply',requested_opening_percent:0,supply_available:0,dry_run:true});
 expect(point.opening).toBe(0);expect(point.supply).toBe(false);expect(point.dryRun).toBe(true);
 const live=livePoint({room_id:'bedroom',available:true,stale:false,received_at:'2026-10-11T12:00:00Z',telemetry_age_seconds:0,reported_target:null,telemetry:{temperature_c:20,target_temperature_c:21,opening_percent:32,status:'active',measurement_age_seconds:0,proportional_percent:0,integral_percent:0,actuator_status:'ready',fault:'none',dry_run:false,hvac_action:'heating'}});
 expect(live?.opening).toBe(32);expect(live?.supply).toBeNull();
});
it('does not shade recording gaps and merges contiguous equal states',()=>{
 const points=[0,1,2,3,4].map(at=>({at,temperature:20,target:20,opening:0,status:'outside_supply',supply:false,breakBefore:at===3}));
 expect(statusBands(points).map(b=>[b.from,b.to])).toEqual([[0,2],[3,4]]);
});
it('renders a fixed percentage scale and selected demand with dry-run status',()=>{
 const point={at:1,temperature:20,target:21,opening:45,status:'active',supply:true,dryRun:true,breakBefore:true};
 render(<ValveDemandChart points={[point]} selected={point} from={0} to={2} inspect={()=>{}} time={()=>'12:00'}/>);
 expect(screen.getByRole('img',{name:/Requested valve opening/})).toBeInTheDocument();
 expect(screen.getByText('100')).toBeInTheDocument();
 expect(screen.getByText(/Requested opening 45.0%.*Dry run/)).toBeInTheDocument();
});
