import { ChevronDown } from 'lucide-react';
import { linePath } from './history';
import type { Point } from './history';
interface Props {
  points: Point[]; selected: Point | null | undefined; from: number; to: number;
  inspect: (clientX:number,left:number,width:number)=>void;
  time: (at:number,detailed?:boolean)=>string;
}
export function piBounds(points: Point[]): [number,number] {
  let low=0,high=0;
  for(const point of points)for(const value of [point.p,point.i])if(value!=null&&Number.isFinite(value)){low=Math.min(low,value);high=Math.max(high,value)}
  const padding=Math.max(1,(high-low)*.08);
  return [Math.floor(low-padding),Math.ceil(high+padding)];
}
export default function PIDiagnostics({points,selected,from,to,inspect,time}:Props){
  const [min,max]=piBounds(points);
  const x=(at:number)=>56+(at-from)/Math.max(1,to-from)*824;
  const y=(value:number)=>230-(value-min)/(max-min)*200;
  const available=points.some(p=>[p.p,p.i].some(v=>v!=null&&Number.isFinite(v)));
  const format=(value:number|null|undefined)=>value==null?'unavailable':`${value.toFixed(1)} pp`;
  return <details className="pi-history">
    <summary><span>PI diagnostics <small>P and I contributions</small></span><ChevronDown size={18}/></summary>
    <p className="history-note-inline">P responds to the current temperature error. I accumulates error over time. Values are percentage-point contributions to valve demand.</p>
    <div className="history-legend pi-legend"><span className="p-key">P contribution</span><span className="i-key">I contribution</span></div>
    {available?<svg viewBox="0 0 920 278" className="history-chart" role="img" aria-label="Proportional and integral contributions over time"
      onPointerMove={e=>{const r=e.currentTarget.getBoundingClientRect();inspect(e.clientX,r.left,r.width)}}
      onPointerDown={e=>{const r=e.currentTarget.getBoundingClientRect();inspect(e.clientX,r.left,r.width)}}>
      <text x="15" y="16">pp</text>
      {[0,1,2,3,4].map(i=>{const value=min+(max-min)*i/4;return <g key={i}><line x1="56" x2="880" y1={y(value)} y2={y(value)} className="grid-line"/><text x="45" y={y(value)+4} textAnchor="end">{value.toFixed(1)}</text></g>})}
      <line x1="56" x2="880" y1={y(0)} y2={y(0)} className="pi-zero"/>
      <path d={linePath(points,'p',x,y)} className="p-line"/><path d={linePath(points,'i',x,y)} className="i-line"/>
      {[0,1,2,3,4].map(i=><text key={i} x={56+824*i/4} y="260" textAnchor={i===0?'start':i===4?'end':'middle'}>{time(from+(to-from)*i/4)}</text>)}
      {selected&&<g><line x1={x(selected.at)} x2={x(selected.at)} y1="30" y2="230" className="cursor-line"/>{selected.p!=null&&<circle cx={x(selected.at)} cy={y(selected.p)} r="4" fill="#3976ad"/>}{selected.i!=null&&<circle cx={x(selected.at)} cy={y(selected.i)} r="4" fill="#9652a0"/>}</g>}
    </svg>:<p className="history-message">No PI contributions available in this time range.</p>}
    <p className="history-readout" aria-live="polite">{selected?`${time(selected.at,true)} · P ${format(selected.p)} · I ${format(selected.i)}${selected.dryRun?' · Dry run':''}`:'Inspect any chart or use the shared sample slider to compare P and I with temperature and valve demand.'}</p>
    <p className="history-note-inline">Negative contributions are shown below zero. Output limits and control schedules can make requested opening differ from P + I.</p>
  </details>;
}
