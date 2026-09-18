import { render, screen, fireEvent } from '@testing-library/react';
import '@testing-library/jest-dom';
import { SpreadsheetInsightAnomalyCard } from '@/components/agent/chat/SpreadsheetInsightAnomalyCard';
import { SPREADSHEET_HIGHLIGHT_LOCATIONS_EVENT } from '@/lib/agentLaunchContext';
import type { AnomalyItem } from '@/types/agent';

const anomalies: AnomalyItem[] = [
  {
    id: 'anom_0',
    metric: 'Revenue',
    movement: 'UNEXPECTED_SPIKE',
    severity: 'warning',
    current_value: 0,
    previous_value: 0,
    change_percent: 0,
    title: 'Negative revenue',
    description: 'Cell B3 is negative.',
    locations: [{ row: 2, col: 1, a1: 'B3' }],
  },
];

describe('SpreadsheetInsightAnomalyCard', () => {
  it('renders collapsed by default and expands on click', () => {
    render(
      <SpreadsheetInsightAnomalyCard
        anomalies={anomalies}
        spreadsheetId={1}
        sheetId={2}
      />
    );

    expect(screen.getByText(/Anomalies \(1\)/)).toBeInTheDocument();
    expect(screen.queryByText('Negative revenue')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /Anomalies \(1\)/ }));

    expect(screen.getByText('Negative revenue')).toBeInTheDocument();
    expect(screen.getByText('Cell B3 is negative.')).toBeInTheDocument();
    expect(screen.getByText('B3')).toBeInTheDocument();
  });

  it('dispatches highlight event when anomaly row is clicked', () => {
    const handler = jest.fn();
    window.addEventListener(SPREADSHEET_HIGHLIGHT_LOCATIONS_EVENT, handler);

    render(
      <SpreadsheetInsightAnomalyCard
        anomalies={anomalies}
        spreadsheetId={5}
        sheetId={9}
      />
    );

    fireEvent.click(screen.getByRole('button', { name: /Anomalies \(1\)/ }));
    fireEvent.click(screen.getByRole('button', { name: /Negative revenue/i }));

    expect(handler).toHaveBeenCalledTimes(1);
    const event = handler.mock.calls[0][0] as CustomEvent;
    expect(event.detail).toEqual({
      spreadsheetId: 5,
      sheetId: 9,
      locations: [{ row: 2, col: 1, a1: 'B3' }],
    });

    window.removeEventListener(SPREADSHEET_HIGHLIGHT_LOCATIONS_EVENT, handler);
  });
});
