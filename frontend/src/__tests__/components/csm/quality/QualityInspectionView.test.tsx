import React from 'react';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import '@testing-library/jest-dom';

import QualityInspectionView from '@/components/csm/quality/QualityInspectionView';
import CsmQualityAPI from '@/lib/api/csmQualityApi';

jest.mock('@/lib/api/csmQualityApi', () => ({
  __esModule: true,
  default: {
    getFilterOptions: jest.fn(),
    listConversations: jest.fn(),
    getReport: jest.fn(),
    getConversation: jest.fn(),
    saveReview: jest.fn(),
  },
}));

const setTab = jest.fn();
jest.mock('@/hooks/useQualityFilterParams', () => ({
  __esModule: true,
  default: () => ({
    filters: {
      agent: [], queue: [], channel: [], customer: [], tag: [], status: [],
    },
    tab: 'conversations',
    page: 1,
    setFilters: jest.fn(),
    setTab: (...args: unknown[]) => setTab(...args),
    setPage: jest.fn(),
    clearFilters: jest.fn(),
    activeFilterCount: 0,
  }),
}));

jest.mock('react-hot-toast', () => ({
  __esModule: true,
  default: { success: jest.fn(), error: jest.fn(), loading: jest.fn() },
}));

const api = CsmQualityAPI as unknown as Record<string, jest.Mock>;

const OPTIONS = {
  organisations: [],
  queues: [],
  agents: [],
  unassigned_count: 0,
  unassigned_review_count: 0,
  channels: [],
  statuses: [],
  tags: [],
  customers: [],
};

const ROW = {
  id: 412,
  tags: ['Refund not received'],
  customer_name: 'Ada Lovelace',
  queue_name: 'T1',
  channel_display: 'Email',
  status_display: 'Closed',
  started_at: '2026-09-17T09:59:00Z',
  assigned_to_name: 'Grace H.',
  review_count: 0,
  my_review: null,
};

beforeEach(() => {
  jest.clearAllMocks();
  api.getFilterOptions.mockResolvedValue(OPTIONS);
  api.listConversations.mockResolvedValue({ results: [ROW], count: 1 });
  api.getReport.mockResolvedValue(null);
  api.getConversation.mockResolvedValue({
    ...ROW, messages: [], reviews: [], my_review: null,
  });
  api.saveReview.mockResolvedValue({ id: 1, rating: 'good' });
});

describe('QualityInspectionView — refreshing after an annotation', () => {
  it('refetches the filter tallies when a review is saved', async () => {
    // The tallies are annotation counts on the Report tab, so leaving them at
    // their mount-time values shows stale numbers until a page reload.
    render(<QualityInspectionView />);
    await waitFor(() => expect(api.getFilterOptions).toHaveBeenCalledTimes(1));

    fireEvent.click(await screen.findByRole('button', { name: 'Refund not received' }));
    const drawer = await screen.findByRole('dialog', { name: 'Review conversation' });

    fireEvent.click(await within(drawer).findByRole('radio', { name: 'Good' }));
    fireEvent.click(within(drawer).getByRole('button', { name: /save annotation/i }));

    await waitFor(() => expect(api.saveReview).toHaveBeenCalled());
    await waitFor(() => expect(api.getFilterOptions).toHaveBeenCalledTimes(2));
    expect(api.listConversations).toHaveBeenCalledTimes(2);
  });
});
