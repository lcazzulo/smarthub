// Inverse August–Roche–Magnus equation (temperature in Celsius, RH in percent).
// https://qed.epa.gov/hms/meteorology/humidity/algorithms/
export function dewPoint(temperature: number | null | undefined, humidity: number | null | undefined): number | null {
  if (temperature == null || humidity == null || !Number.isFinite(temperature)
      || !Number.isFinite(humidity) || temperature <= -243.04 || humidity <= 0 || humidity > 100) return null;
  const gamma = Math.log(humidity / 100) + 17.625 * temperature / (243.04 + temperature);
  const result = 243.04 * gamma / (17.625 - gamma);
  return Number.isFinite(result) ? result : null;
}

export function dewPointComfort(value: number) {
  if (value < 10) return { label: 'Very dry', tone: 'dry' };
  if (value <= 16) return { label: 'Comfortable', tone: 'normal' };
  if (value <= 20) return { label: 'Somewhat humid', tone: 'humid' };
  if (value <= 27) return { label: 'Uncomfortably humid / muggy', tone: 'wet' };
  return { label: 'Oppressive', tone: 'oppressive' };
}
