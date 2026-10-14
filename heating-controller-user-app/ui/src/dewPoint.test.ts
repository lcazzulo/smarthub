import { expect, it } from 'vitest';
import { dewPoint, dewPointComfort } from './dewPoint';

it('calculates a known room example and saturation', () => {
  expect(dewPoint(20, 50)).toBeCloseTo(9.26, 2);
  expect(dewPoint(25, 100)).toBeCloseTo(25, 10);
  expect(dewPoint(0, 100)).toBe(0);
});
it('does not assign dew points to unavailable or invalid measurements', () => {
  for (const humidity of [null, undefined, 0, -1, 101, NaN, Infinity]) expect(dewPoint(20, humidity)).toBeNull();
  for (const temperature of [null, undefined, NaN, Infinity, -243.04]) expect(dewPoint(temperature, 50)).toBeNull();
});
it('uses the requested thresholds, including their boundaries', () => {
  expect(dewPointComfort(9.999).label).toBe('Very dry');
  expect(dewPointComfort(10).label).toBe('Comfortable');
  expect(dewPointComfort(16).label).toBe('Comfortable');
  expect(dewPointComfort(16.001).label).toBe('Somewhat humid');
  expect(dewPointComfort(20).label).toBe('Somewhat humid');
  expect(dewPointComfort(20.001).label).toBe('Uncomfortably humid / muggy');
  expect(dewPointComfort(27).label).toBe('Uncomfortably humid / muggy');
  expect(dewPointComfort(27.001).label).toBe('Oppressive');
});
