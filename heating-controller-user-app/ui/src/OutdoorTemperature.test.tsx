import { render, screen } from '@testing-library/react';
import { expect, it } from 'vitest';
import OutdoorTemperature from './OutdoorTemperature';

it('shows the outdoor reading and its timestamp', () => {
  render(<OutdoorTemperature reading={{ temperature_c: -3.2, received_at: '2026-10-12T10:00:00Z' }}/>);
  expect(screen.getByText('-3.2 °C')).toBeInTheDocument();
  expect(screen.getByRole('region', { name: 'Outside temperature' }).querySelector('time')).toHaveAttribute('datetime', '2026-10-12T10:00:00Z');
});
it('shows zero degrees as a valid reading', () => {
  render(<OutdoorTemperature reading={{ temperature_c: 0, received_at: '2026-10-12T10:00:00Z' }}/>);
  expect(screen.getByText('0.0 °C')).toBeInTheDocument();
});
it('shows a waiting state when no outdoor reading exists', () => {
  render(<OutdoorTemperature/>);
  expect(screen.getByText('Waiting for a sensor reading')).toBeInTheDocument();
});
