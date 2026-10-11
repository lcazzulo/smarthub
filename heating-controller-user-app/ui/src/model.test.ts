import { describe, it, expect } from 'vitest';
import { initialState, reducer, canControl } from './model';
import type { StreamEvent, Snapshot } from './types';
const snapshot: Snapshot = {room_id:'bathroom',available:true,stale:false,received_at:null,telemetry_age_seconds:0,telemetry:null,reported_target:null};
const event = (type: StreamEvent['type'], sequence: number, data: unknown, room_id: string | null = 'bathroom'): StreamEvent => ({version:1,type,sequence,data,room_id,stream_id:'one',sent_at:''});
function connected() {
  let state = reducer(initialState,{type:'select',room:'bathroom'});
  state = reducer(state,{type:'connection',value:'connected'});
  state = reducer(state,{type:'event',event:event('system.state',1,{mqtt_connected:true,controller_online:true},null),now:0});
  return reducer(state,{type:'event',event:event('room.state',2,snapshot),now:0});
}
describe('live room state',()=>{
  it('disables commands while offline or stale',()=>{
    const state=connected(); expect(canControl(state,true)).toBe(true);
    expect(canControl(reducer(state,{type:'connection',value:'disconnected'}),true)).toBe(false);
    expect(canControl({...state,snapshot:{...snapshot,stale:true}},true)).toBe(false);
    expect(canControl(state,false)).toBe(false);
  });
  it('clears previous room and ignores its late messages',()=>{
    const state=reducer(connected(),{type:'select',room:'bedroom'});
    expect(reducer(state,{type:'event',event:event('room.state',3,snapshot),now:1}).snapshot).toBeNull();
  });
  it('does not confirm on HTTP submission or retained reports',()=>{
    let state=reducer(connected(),{type:'request',room:'bathroom',value:21,now:10});
    state=reducer(state,{type:'submitted',room:'bathroom',startedAt:10});
    state=reducer(state,{type:'event',event:event('room.target',3,{temperature_c:21,received_at:'',retained:true}),now:20});
    expect(state.commands.bathroom.stage).toBe('awaiting');
    state=reducer(state,{type:'event',event:event('room.target',4,{temperature_c:21,received_at:'',retained:false}),now:30});
    expect(state.commands.bathroom.stage).toBe('reported');
  });
  it('keeps an early MQTT report when HTTP completes later',()=>{
    let state=reducer(connected(),{type:'request',room:'bathroom',value:21,now:10});
    state=reducer(state,{type:'event',event:event('room.target',3,{temperature_c:21,received_at:'',retained:false}),now:20});
    state=reducer(state,{type:'submitted',room:'bathroom',startedAt:10});
    expect(state.commands.bathroom.stage).toBe('reported');
  });
  it('times out without resending and marks interrupted requests uncertain',()=>{
    const state=reducer(connected(),{type:'request',room:'bathroom',value:21,now:10});
    expect(reducer(state,{type:'tick',now:21011}).commands.bathroom.stage).toBe('unconfirmed');
    expect(reducer(state,{type:'connection',value:'disconnected'}).commands.bathroom.stage).toBe('unconfirmed');
  });
  it('rejects out-of-order updates and accepts a restarted stream',()=>{
    const state=connected();
    expect(reducer(state,{type:'event',event:event('room.state',1,{...snapshot,stale:true}),now:1})).toBe(state);
    const e={...event('system.state',1,{mqtt_connected:true,controller_online:true},null),stream_id:'two'};
    expect(reducer(state,{type:'event',event:e,now:2}).snapshot).toBeNull();
  });
});
