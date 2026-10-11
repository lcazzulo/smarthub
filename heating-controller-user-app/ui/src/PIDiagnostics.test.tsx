import { render, screen, fireEvent } from '@testing-library/react';
import { expect, it } from 'vitest';
import PIDiagnostics, {piBounds} from './PIDiagnostics';
import { historyPoint, reducePoints, linePath } from './history';
it('preserves signed PI values, extrema and missing-data breaks',()=>{
 const point=historyPoint({at:'2026-10-11T12:00:00Z',temperature_c:22,target_temperature_c:20,break_before:true,control_status:'active',proportional_percent:-35,integral_percent:12});
 expect(point.p).toBe(-35);expect(point.i).toBe(12);
 const points=Array.from({length:100},(_,at)=>({...point,at,breakBefore:false,p:at===25?-90:0,i:at===50?null:at===75?150:0}));
 const reduced=reducePoints(points,0,100,1);
 expect(reduced.some(p=>p.p===-90)).toBe(true);expect(reduced.some(p=>p.i===150)).toBe(true);
 expect(linePath(reduced,'i',x=>x,y=>y).match(/M/g)).toHaveLength(2);
 const [low,high]=piBounds(reduced);expect(low).toBeLessThan(-90);expect(high).toBeGreaterThan(150);
});
it('starts collapsed and displays signed contributions when expanded',()=>{
 const point={at:1,temperature:20,target:21,p:-25,i:10,breakBefore:true};
 const {container}=render(<PIDiagnostics points={[point]} selected={point} from={0} to={2} inspect={()=>{}} time={()=>'12:00'}/>);
 expect(container.querySelector('details')).not.toHaveAttribute('open');
 fireEvent.click(screen.getByText('PI diagnostics'));
 expect(container.querySelector('details')).toHaveAttribute('open');
 expect(screen.getByRole('img',{name:'Proportional and integral contributions over time'})).toBeInTheDocument();
 expect(screen.getByText(/P -25.0 pp · I 10.0 pp/)).toBeInTheDocument();
});
it('shows missing PI data without substituting zero',()=>{
 const point={at:1,temperature:20,target:21,p:null,i:null,breakBefore:true};
 render(<PIDiagnostics points={[point]} selected={point} from={0} to={2} inspect={()=>{}} time={()=>'12:00'}/>);
 fireEvent.click(screen.getByText('PI diagnostics'));
 expect(screen.getByText('No PI contributions available in this time range.')).toBeInTheDocument();
 expect(screen.getByText(/P unavailable · I unavailable/)).toBeInTheDocument();
 expect(piBounds([point])).toEqual([-1,1]);
});
