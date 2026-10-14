import { Thermometer } from 'lucide-react';
import type { OutdoorReading } from './types';

export default function OutdoorTemperature({ reading, onHistory }: { reading?: OutdoorReading | null; onHistory?: () => void }) {
  const valid = reading != null && Number.isFinite(reading.temperature_c) && Number.isFinite(Date.parse(reading.received_at));
  return <section className="outdoor-temperature" aria-label="Outside temperature">
    <div className="outdoor-heading"><Thermometer size={20} aria-hidden="true"/><span>Outside temperature</span></div>
    <button className="outdoor-value sensor-history-trigger" aria-label="Show outside temperature history" aria-haspopup="dialog" onClick={onHistory}>{valid ? `${reading.temperature_c.toFixed(1)} °C` : '—'}</button>
    <span className="outdoor-time">{valid ? <>Last reading <time dateTime={reading.received_at}>{new Date(reading.received_at).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })}</time></> : 'Waiting for a sensor reading'}</span>
  </section>;
}
