export interface Room {
  id: string;
  name: string;
  enabled: boolean;
  target_min_c: number;
  target_max_c: number;
  target_step_c: number;
}
export interface TargetReport {
  temperature_c: number;
  received_at: string;
  retained: boolean;
}
export interface Telemetry {
  temperature_c: number | null;
  target_temperature_c: number;
  opening_percent: number;
  status: string;
  measurement_age_seconds: number | null;
  proportional_percent: number | null;
  integral_percent: number | null;
  actuator_status: string;
  fault: string;
  dry_run: boolean;
  hvac_action: string;
  recording_status?: string;
  recording_fault?: string | null;
  recording_dropped_records?: number;
}
export interface Snapshot {
  room_id: string;
  available: boolean;
  stale: boolean;
  received_at: string | null;
  telemetry_age_seconds: number | null;
  telemetry: Telemetry | null;
  reported_target: TargetReport | null;
}
export interface SystemState { mqtt_connected: boolean; controller_online: boolean }
export interface StreamEvent {
  version: number;
  type: 'system.state' | 'room.state' | 'room.target' | 'error';
  stream_id: string;
  sequence: number;
  sent_at: string;
  room_id: string | null;
  data: unknown;
}
export type CommandStage = 'sending' | 'awaiting' | 'reported' | 'unconfirmed' | 'error';
export interface Command {
  value: number;
  stage: CommandStage;
  startedAt: number;
  afterSequence: number;
  streamId: string | null;
  message: string;
}
