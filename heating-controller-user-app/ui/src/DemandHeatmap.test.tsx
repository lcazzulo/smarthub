import { fireEvent, render, screen } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import DemandHeatmap, { openingColor, type HeatCell } from './DemandHeatmap';
const cell: HeatCell = { minute: 0, fold: 0, offset: '+0100', repeated: false, coverage_seconds: 300, active_seconds: 300, simulated_seconds: 0, elapsed_seconds: 300, unknown_seconds: 0, opening_percent: 25 };
it('places midnight at the bottom and the final strip at the top with blue-to-red coloring', () => {
  const { container } = render(<DemandHeatmap days={[{ date: '2026-01-10', timeline: [{ ...cell, opening_percent: 1 }, { ...cell, minute: 1435, opening_percent: 100 }] }]} selectedDay={0} onSelectDay={() => {}}/>);
  expect(screen.getByText('00:00')).toBeInTheDocument();
  expect(screen.getByText('23:59')).toBeInTheDocument();
  const blue = container.querySelector(`rect[fill="${openingColor(1)}"]`);
  const red = container.querySelector(`rect[fill="${openingColor(100)}"]`);
  expect(blue).toHaveAttribute('y', '1435');
  expect(red).toHaveAttribute('y', '0');
  fireEvent.change(screen.getByRole('slider'), { target: { value: '1435' } });
  expect(container.querySelector('.history-readout')).toHaveTextContent('23:55–24:00');
  expect(container.querySelector('.history-readout')).toHaveTextContent('100.0%');
});
it('distinguishes closed, missing, simulation and future portions without coloring them as heating', () => {
  const { container } = render(<DemandHeatmap days={[{ date: '2026-01-10', timeline: [{ ...cell, coverage_seconds: 60, active_seconds: 0, opening_percent: null, simulated_seconds: 60, unknown_seconds: 60, elapsed_seconds: 180 }] }]} selectedDay={0} onSelectDay={() => {}}/>);
  expect(container.querySelector('g .heat-closed')).toHaveAttribute('width', '20');
  expect(container.querySelector('g .heat-sim')).toHaveAttribute('width', '20');
  expect(container.querySelector('g rect[fill^="url"]')).toHaveAttribute('width', '20');
  expect(container.querySelector('.history-readout')).toHaveTextContent('Closed 1.0 min');
  expect(container.querySelector('.history-readout')).toHaveTextContent('Missing 1.0 min');
  expect(container.querySelector('.history-readout')).toHaveTextContent('Not yet elapsed 2.0 min');
});
it('supports day selection and inspecting both occurrences of the repeated hour', () => {
  const choose = vi.fn();
  const { container } = render(<DemandHeatmap days={[{ date: '2026-10-25', timeline: [{ ...cell, minute: 120, repeated: true, offset: '+0200' }, { ...cell, minute: 120, repeated: true, fold: 1, opening_percent: 100 }] }]} selectedDay={0} onSelectDay={choose}/>);
  fireEvent.click(screen.getByRole('button', { name: 'Inspect heatmap 2026-10-25' }));
  expect(choose).toHaveBeenCalledWith(0);
  fireEvent.change(screen.getByRole('slider'), { target: { value: '120' } });
  expect(container.querySelector('.history-readout')).toHaveTextContent('First occurrence');
  fireEvent.change(screen.getByRole('combobox'), { target: { value: '1' } });
  expect(container.querySelector('.history-readout')).toHaveTextContent('Second occurrence');
  expect(container.querySelector('.history-readout')).toHaveTextContent('100.0%');
  expect(container.querySelector(`rect[fill="${openingColor(100)}"]`)).toHaveAttribute('x', '50');
});
