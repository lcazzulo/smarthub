import PIDiagnostics from './PIDiagnostics';
import ValveDemandChart from './ValveDemandChart';
import { useEffect, useMemo, useRef, useState } from 'react';
import { linePath, livePoint, loadHistory, reducePoints } from './history';
import type { Point } from './history';
import type { Snapshot } from './types';
import './history.css';
const time = (at:number, detailed=false) => new Intl.DateTimeFormat('en-GB', {timeZone:'Europe/Rome', month:'short',day:'2-digit',hour:'2-digit',minute:'2-digit', ...(detailed ? {second:'2-digit',timeZoneName:'shortOffset' as const} : {})}).format(at);
export default function TemperatureHistory({room, snapshot, online, streamId}: {room:string; snapshot:Snapshot|null;online:boolean;streamId:string|null}) {
  const [hours,setHours]=useState(24);
  const [follow,setFollow]=useState(true);
  const [revision,setRevision]=useState(0);
  const [data,setData]=useState<{room:string; points:Point[]; from:number; to:number}>({room:'',points:[],from:0,to:1});
  const [loading,setLoading]=useState(true);
  const [error,setError]=useState<string|null>(null);
  const [cursor,setCursor]=useState<number|null>(null);
  const pending=useRef<Point[]>([]);
  const broken=useRef(true);
  const lastSeen=useRef('');
  const wasOnline=useRef(online);
  const previousStream=useRef(streamId);
  // One backfill on reconnection; no polling while connected.
  useEffect(()=>{ if (online && (!wasOnline.current || previousStream.current!==streamId) && follow) setRevision(v=>v+1); if (!online) broken.current=true; wasOnline.current=online; previousStream.current=streamId; },[online,follow,streamId]);
  useEffect(()=>{
    const abort=new AbortController();
    const to=Date.now(),from=to-hours*3600000;
    pending.current=[]; broken.current=true; lastSeen.current=''; setLoading(true);setError(null);setCursor(null);
    setData({room,points:[],from,to});
    loadHistory(room,from,to,abort.signal).then(points=>{
      if(abort.signal.aborted)return;
      const tail=pending.current.filter(p=>p.at>=to);
      if(tail.length)tail[0]={...tail[0],breakBefore:true};
      const end=tail.at(-1)?.at ?? to;
      setData({room,points:[...points,...tail].filter(p=>p.at>=end-hours*3600000),from:follow?end-hours*3600000:from,to:end});setLoading(false);
    }).catch(e=>{if(!abort.signal.aborted){setError(e.message);setLoading(false)}});
    return ()=>abort.abort();
  },[room,hours,revision]);
  useEffect(()=>{
    if(!follow || !online || snapshot?.room_id!==room)return;
    const p=livePoint(snapshot);
    if(!p){broken.current=true;return;}
    if(snapshot.received_at===lastSeen.current)return;
    lastSeen.current=snapshot.received_at!;
    p.breakBefore=broken.current;broken.current=false;
    pending.current.push(p);
    if(pending.current.length>25000)pending.current.shift();
    setData(old=>{
      if(old.room!==room || p.at<old.to)return old;
      const last=old.points.at(-1);
      if(last?.at===p.at)return old;
      p.breakBefore ||= !last?.live || p.at-last.at>90000;
      return {room,from:p.at-hours*3600000,to:p.at,points:[...old.points.filter(v=>v.at>=p.at-hours*3600000),p]};
    });
  },[snapshot,follow,online,room,hours]);
  const points=data.room===room?data.points:[];
  const reduced=useMemo(()=>reducePoints(points,data.from,data.to),[points,data.from,data.to]);
  const values=reduced.flatMap(p=>[p.temperature,p.target]).filter((v):v is number=>v!=null&&Number.isFinite(v));
  let min=values.length?Math.floor(Math.min(...values)-.5):15,max=values.length?Math.ceil(Math.max(...values)+.5):25;
  if(max-min<2){min-=1;max+=1}
  const x=(at:number)=>56+(at-data.from)/Math.max(1,data.to-data.from)*824;
  const y=(v:number)=>230-(v-min)/(max-min)*200;
  const selected=cursor==null?null:points[Math.min(cursor,points.length-1)];
  function inspect(clientX:number,left:number,width:number){
    const at=data.from+Math.max(0,Math.min(1,((clientX-left)/width*920-56)/824))*(data.to-data.from);
    let lo=0,hi=points.length-1;while(lo<hi){const m=Math.floor((lo+hi)/2);if(points[m].at<at)lo=m+1;else hi=m}
    if(lo>0 && at-points[lo-1].at<points[lo].at-at)lo--;setCursor(lo);
  }
  return <section className="panel temperature-history" aria-label="Temperature history">
    <div className="history-heading"><div><h2>Temperature history</h2><p>Room temperature and controller target</p></div><div className="history-ranges" role="group" aria-label="History time range">{[1,6,24,168].map(h=><button key={h} aria-pressed={hours===h} onClick={()=>setHours(h)}>{h===168?'7d':`${h}h`}</button>)}</div></div>
    <div className="history-options"><div className="history-legend"><span className="actual-key">Temperature</span><span className="target-key">Target</span></div><label><input type="checkbox" checked={follow} onChange={e=>{setFollow(e.target.checked);if(e.target.checked)setRevision(v=>v+1)}}/> Follow live</label><button onClick={()=>setRevision(v=>v+1)}>Refresh</button></div>
    {loading && <p role="status" className="history-message">Loading recorded history…</p>}
    {error && <p role="alert" className="history-message">{error} <button onClick={()=>setRevision(v=>v+1)}>Retry</button></p>}
    {!loading&&!error&&!points.length&&<p className="history-message">No recorded samples in this time range.</p>}
    {!loading&&!error&&points.length>0&&<>
      <svg viewBox="0 0 920 278" className="history-chart" role="img" aria-label="Temperature and target in degrees Celsius over time" onPointerMove={e=>{const r=e.currentTarget.getBoundingClientRect();inspect(e.clientX,r.left,r.width)}} onPointerDown={e=>{const r=e.currentTarget.getBoundingClientRect();inspect(e.clientX,r.left,r.width)}}>
        <text x="15" y="16">°C</text>
        {[0,1,2,3,4].map(i=>{const v=min+(max-min)*i/4;return <g key={i}><line x1="56" x2="880" y1={y(v)} y2={y(v)} className="grid-line"/><text x="45" y={y(v)+4} textAnchor="end">{v.toFixed(1)}</text></g>})}
        {[0,1,2,3,4].map(i=><text key={i} x={56+824*i/4} y="260" textAnchor={i===0?'start':i===4?'end':'middle'}>{time(data.from+(data.to-data.from)*i/4)}</text>)}
        <path d={linePath(reduced,'target',x,y)} className="target-line"/><path d={linePath(reduced,'temperature',x,y)} className="actual-line"/>
        {selected && <g><line x1={x(selected.at)} x2={x(selected.at)} y1="30" y2="230" className="cursor-line"/>{selected.temperature!=null&&<circle cx={x(selected.at)} cy={y(selected.temperature)} r="4" fill="#34796a"/>}{selected.target!=null&&<circle cx={x(selected.at)} cy={y(selected.target)} r="4" fill="#be6828"/>}</g>}
      </svg>
      <ValveDemandChart points={reduced} selected={selected} from={data.from} to={data.to} inspect={inspect} time={time}/>
      <PIDiagnostics key={room} points={reduced} selected={selected} from={data.from} to={data.to} inspect={inspect} time={time}/>
      <label className="history-scrubber">Inspect a sample<input aria-label="Inspect history sample" type="range" min={0} max={points.length-1} value={cursor??points.length-1} onChange={e=>setCursor(Number(e.target.value))}/></label>
      <p className="history-readout" aria-live="polite">{selected?`${time(selected.at,true)} · Temperature ${selected.temperature==null?'unavailable':selected.temperature.toFixed(1)+' °C'} · Target ${selected.target==null?'unavailable':selected.target.toFixed(1)+' °C'}${selected.live?' · Live telemetry':''}`:'Hover, tap, or use the slider to inspect a sample.'}</p>
    </>}
    <p className="history-note">Europe/Rome · Gaps mark missing data or controller restarts. {follow?(online?'Following live telemetry.':'Waiting for connection.'): 'Live updates paused.'}</p>
  </section>;
}
