import { afterEach, expect, it, vi } from 'vitest';
import { historyPoint, linePath, loadHistory, reducePoints } from './history';
afterEach(()=>vi.unstubAllGlobals());
it('follows all pages with fixed boundaries and preserves server gaps',async()=>{
  const fetch=vi.fn().mockResolvedValueOnce({ok:true,json:async()=>({samples:[{at:'2026-10-11T00:00:00Z',temperature_c:22,target_temperature_c:20,break_before:true}],next_cursor:'next'})}).mockResolvedValueOnce({ok:true,json:async()=>({samples:[{at:'2026-10-11T00:01:00Z',temperature_c:23,target_temperature_c:20,break_before:false}],next_cursor:null})});
  vi.stubGlobal('fetch',fetch);
  const points=await loadHistory('bathroom',1000,10000,new AbortController().signal);
  expect(points).toHaveLength(2);expect(points[0].breakBefore).toBe(true);
  const a=new URL(fetch.mock.calls[0][0],'http://localhost');const b=new URL(fetch.mock.calls[1][0],'http://localhost');
  expect(b.searchParams.get('cursor')).toBe('next');expect(a.searchParams.get('to')).toBe(b.searchParams.get('to'));
});
it('does not chart a stale measurement as current',()=>{
  expect(historyPoint({at:'2026-10-11T00:00:00Z',temperature_c:22,target_temperature_c:20,break_before:false,control_status:'stale_temperature'}).temperature).toBeNull();
});
it('preserves extrema and gaps when reducing long ranges',()=>{
  const points=Array.from({length:100},(_,i)=>({at:i,temperature:i===51?35:20,target:i===60?24:19,breakBefore:i===70}));
  const result=reducePoints(points,0,100,1);
  expect(result.some(p=>p.temperature===35)).toBe(true);expect(result.some(p=>p.target===24)).toBe(true);expect(result.some(p=>p.at===70&&p.breakBefore)).toBe(true);
  const path=linePath(result,'temperature',x=>x,y=>y);expect(path.match(/M/g)).toHaveLength(2);
});
it('uses steps for targets and breaks both series at missing values',()=>{
  const points=[{at:0,temperature:20,target:20,breakBefore:true},{at:1,temperature:21,target:22,breakBefore:false},{at:2,temperature:null,target:null,breakBefore:false},{at:3,temperature:22,target:21,breakBefore:false}];
  expect(linePath(points,'target',x=>x,y=>y)).toContain('H1V22');
  expect(linePath(points,'temperature',x=>x,y=>y).match(/M/g)).toHaveLength(2);
});
it('keeps valve demand peaks, nulls and control status transitions',()=>{
  const points=Array.from({length:100},(_,i)=>({at:i,temperature:20,target:20,opening:i===25?90:i===50?null:0,status:i<75?'active':'outside_supply',supply:i<75,breakBefore:false}));
  const reduced=reducePoints(points,0,100,1);
  expect(reduced.some(p=>p.opening===90)).toBe(true);
  expect(reduced.some(p=>p.opening===null)).toBe(true);
  expect(reduced.some(p=>p.at===74)).toBe(true);
  expect(reduced.some(p=>p.at===75)).toBe(true);
  expect(linePath(reduced,'opening',v=>v,v=>v).match(/M/g)).toHaveLength(2);
});
