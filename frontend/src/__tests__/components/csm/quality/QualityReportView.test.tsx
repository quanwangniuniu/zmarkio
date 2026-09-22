import React from 'react';
import { render, screen } from '@testing-library/react';
import '@testing-library/jest-dom';

import QualityReportView from '@/components/csm/quality/QualityReportView';
import type { QualityReport } from '@/types/csmQuality';

const report: QualityReport = {
  filters_echo: { bucket: 'day', date_basis: 'review' },
  totals: {
    reviews: 10,
    conversations_reviewed: 8,
    conversations_in_scope: 40,
    coverage_pct: 20,
  },
  by_rating: [
    { rating: 'good', rating_display: 'Good', count: 6, pct: 60 },
    { rating: 'needs_improvement', rating_display: 'Needs Improvement', count: 3, pct: 30 },
    { rating: 'poor', rating_display: 'Poor', count: 1, pct: 10 },
  ],
  by_agent: [
    { agent_user_id: 9, agent_name: 'Ada L.', total: 7, good: 5, needs_improvement: 2, poor: 0 },
    { agent_user_id: null, agent_name: 'Unassigned', total: 3, good: 1, needs_improvement: 1, poor: 1 },
  ],
  by_date: [
    { bucket: '2026-03-01', total: 4, good: 3, needs_improvement: 1, poor: 0 },
    { bucket: '2026-03-02', total: 6, good: 3, needs_improvement: 2, poor: 1 },
  ],
  generated_at: '2026-03-02T12:00:00Z',
};

describe('QualityReportView — AC4 aggregated counts', () => {
  it('renders the rating split, coverage, agents and buckets', () => {
    render(<QualityReportView report={report} loading={false} />);

    // 'Good' also labels the chart legend, so assert on the tile's own numbers.
    expect(screen.getByText('60% of annotations')).toBeInTheDocument();
    expect(screen.getByText('30% of annotations')).toBeInTheDocument();
    expect(screen.getByText('10% of annotations')).toBeInTheDocument();
    expect(screen.getByText('20%')).toBeInTheDocument();
    expect(screen.getByText('8 of 40 conversations reviewed')).toBeInTheDocument();

    const agentTable = screen.getByRole('table', { name: /annotation counts per agent/i });
    expect(agentTable).toHaveTextContent('Ada L.');
    expect(agentTable).toHaveTextContent('Unassigned');

    expect(screen.getByText('2026-03-01')).toBeInTheDocument();
    expect(screen.getByText(/grouped by day/i)).toBeInTheDocument();
  });

  it('shows a skeleton while loading', () => {
    render(<QualityReportView report={null} loading />);

    expect(screen.getByLabelText('Loading report')).toBeInTheDocument();
  });

  it('renders an empty agent table without crashing', () => {
    render(
      <QualityReportView
        report={{ ...report, by_agent: [], by_date: [] }}
        loading={false}
      />,
    );

    expect(screen.getByText(/no annotations for these filters yet/i)).toBeInTheDocument();
  });
});
