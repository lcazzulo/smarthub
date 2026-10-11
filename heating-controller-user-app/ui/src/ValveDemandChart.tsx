import { linePath, statusBands } from './history';
import type { Point } from './history';
interface Props {
  points: Point[]; selected: Point | null | undefined; from: number; to: number;
  inspect: (clientX:number,left:number,width:number)=>void;
  time: (at:number,detailed?:boolean)=>string;
}
export default function ValveDemandChart({points,selected,from,to,inspect,time}:Props) {
  const x=(at:number)=>56+(at-from)/Math.max(1,to-from)*824;
  const y=(value:number)=>230-value*2;
  const bands=statusBands(points);
  return <div className="valve-history">
    <h3>Valve demand</h3><p className="history-note-inline">Requested opening · controller demand, not measured valve position</p>
    <svg viewBox="0 0 920 308" className="history-chart" role="img" aria-label="Requested valve opening percentage and control status over time"
      onPointerMove={e=>{const r=e.currentTarget.getBoundingClientRect();inspect(e.clientX,r.left,r.width)}}
      onPointerDown={e=>{const r=e.currentTarget.getBoundingClientRect();inspect(e.clientX,r.left,r.width)}}>
      <text x="15" y="16">%</text>
      {[0,25,50,75,100].map(v=><g key={v}><line x1="56" x2="880" y1={y(v)} y2={y(v)} className="grid-line"/><text x="45" y={y(v)+4} textAnchor="end">{v}</text></g>)}
      {bands.map(b=><g key={b.from}><title>{`${time(b.from,true)} – ${time(b.to,true)} · ${b.label}`}</title><rect x={x(b.from)} y="30" width={x(b.to)-x(b.from)} height="200" className={`status-shade band-${b.tone}`}/><rect x={x(b.from)} y="242" width={x(b.to)-x(b.from)} height="10" className={`status-strip band-${b.tone}`}/></g>)}
      <path d={linePath(points,'opening',x,y)} className="opening-line"/>
      {[0,1,2,3,4].map(i=><text key={i} x={56+824*i/4} y="278" textAnchor={i===0?'start':i===4?'end':'middle'}>{time(from+(to-from)*i/4)}</text>)}
      {selected&&<g><line x1={x(selected.at)} x2={x(selected.at)} y1="30" y2="252" className="cursor-line"/>{selected.opening!=null&&<circle cx={x(selected.at)} cy={y(selected.opening)} r="4" fill="#995719"/>}</g>}
    </svg>
    <div className="status-legend"><span className="band-active">Active</span><span className="band-supply">Outside supply</span><span className="band-schedule">Outside heating schedule</span><span className="band-other">Other / unknown</span></div>
    <p className="history-readout" aria-live="polite">{selected?`${time(selected.at,true)} · Requested opening ${selected.opening==null?'unavailable':selected.opening.toFixed(1)+'%'} · ${selected.status?.replaceAll('_',' ')??'Unknown status'} · ${selected.supply==null?'Supply availability unknown':selected.supply?'Within supply hours':'Outside supply hours'}${selected.dryRun?' · Dry run':''}`:'Inspect either chart to compare temperature and valve demand at the same time.'}</p>
  </div>;
}
