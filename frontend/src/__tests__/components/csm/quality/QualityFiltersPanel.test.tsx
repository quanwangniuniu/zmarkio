import React from 'react';
import { render, screen, fireEvent, within } from '@testing-library/react';
import '@testing-library/jest-dom';

import QualityFiltersPanel from '@/components/csm/quality/QualityFiltersPanel';
import { EMPTY_QUALITY_FILTERS, QualityFilterOptions } from '@/types/csmQuality';

const options: QualityFilterOptions = {
  organisations: [{ id: 1, name: 'Acme' }],
  queues: [
    { id: 3, name: 'T1 Frontline', organisation: 1, is_active: true, conversation_count: 12, review_count: 4 },
    { id: 4, name: 'Retired', organisation: 1, is_active: false, conversation_count: 2, review_count: 0 },
  ],
  agents: [
    { user_id: 9, name: 'Ada L.', email: 'ada@x.io', conversation_count: 5, review_count: 2 },
  ],
  unassigned_count: 3,
  unassigned_review_count: 1,
  channels: [
    { value: 'web', label: 'Web', conversation_count: 8, review_count: 3 },
    { value: 'email', label: 'Email', conversation_count: 6, review_count: 1 },
  ],
  statuses: [
    { value: 'closed', label: 'Closed', conversation_count: 11, review_count: 4 },
    { value: 'active', label: 'Active', conversation_count: 4, review_count: 0 },
  ],
  tags: [
    { value: 'vip', conversation_count: 9, review_count: 3 },
    { value: 'refund', conversation_count: 2, review_count: 0 },
  ],
  customers: [
    { id: 5, name: 'Grace H.', email: 'grace@x.io', conversation_count: 7, review_count: 2 },
    { id: 6, name: 'Quiet Co.', email: 'quiet@x.io', conversation_count: 1, review_count: 0 },
  ],
};

function renderPanel(overrides: Partial<React.ComponentProps<typeof QualityFiltersPanel>> = {}) {
  const onChange = jest.fn();
  const onClear = jest.fn();
  render(
    <QualityFiltersPanel
      filters={EMPTY_QUALITY_FILTERS}
      options={options}
      activeFilterCount={0}
      showDateBasis={false}
      countMode="conversations"
      onChange={onChange}
      onClear={onClear}
      {...overrides}
    />,
  );
  return { onChange, onClear };
}

describe('QualityFiltersPanel — AC2 all six filters', () => {
  it('emits a date range from the two date inputs', () => {
    const { onChange } = renderPanel();

    fireEvent.change(screen.getByLabelText('From'), { target: { value: '2026-03-01' } });
    expect(onChange).toHaveBeenCalledWith({ date_from: '2026-03-01' });

    fireEvent.change(screen.getByLabelText('To'), { target: { value: '2026-03-31' } });
    expect(onChange).toHaveBeenCalledWith({ date_to: '2026-03-31' });
  });

  it('emits a numeric agent id, and the unassigned sentinel', () => {
    const { onChange } = renderPanel();

    fireEvent.click(screen.getByRole('button', { name: 'Agent' }));
    const list = screen.getByRole('listbox', { name: 'Agent' });

    fireEvent.click(within(list).getByLabelText(/Ada L\./));
    expect(onChange).toHaveBeenCalledWith({ agent: [9] });

    fireEvent.click(within(list).getByLabelText(/Unassigned/));
    expect(onChange).toHaveBeenCalledWith({ agent: ['unassigned'] });
  });

  it('emits queue ids and marks archived queues', () => {
    const { onChange } = renderPanel();

    fireEvent.click(screen.getByRole('button', { name: 'Queue' }));
    const list = screen.getByRole('listbox', { name: 'Queue' });

    expect(within(list).getByLabelText(/Retired \(archived\)/)).toBeInTheDocument();
    fireEvent.click(within(list).getByLabelText(/T1 Frontline/));
    expect(onChange).toHaveBeenCalledWith({ queue: [3] });
  });

  it('emits channel, customer, tag and status values', () => {
    const { onChange } = renderPanel();

    fireEvent.click(screen.getByRole('button', { name: 'Channel' }));
    fireEvent.click(within(screen.getByRole('listbox', { name: 'Channel' })).getByLabelText(/Email/));
    expect(onChange).toHaveBeenCalledWith({ channel: ['email'] });

    fireEvent.click(screen.getByRole('button', { name: 'Customer' }));
    fireEvent.click(within(screen.getByRole('listbox', { name: 'Customer' })).getByLabelText(/Grace H\./));
    expect(onChange).toHaveBeenCalledWith({ customer: [5] });

    fireEvent.click(screen.getByRole('button', { name: 'Tag' }));
    fireEvent.click(within(screen.getByRole('listbox', { name: 'Tag' })).getByLabelText(/vip/));
    expect(onChange).toHaveBeenCalledWith({ tag: ['vip'] });

    fireEvent.click(screen.getByRole('button', { name: 'Status' }));
    fireEvent.click(within(screen.getByRole('listbox', { name: 'Status' })).getByLabelText(/Closed/));
    expect(onChange).toHaveBeenCalledWith({ status: ['closed'] });
  });

  it.each([
    ['Customer', /Grace H\..*7 conversations/],
    ['Agent', /Ada L\..*5 conversations/],
    ['Agent', /Unassigned.*3 conversations/],
    ['Queue', /T1 Frontline.*12 conversations/],
    ['Channel', /Email.*6 conversations/],
    ['Tag', /vip.*9 conversations/],
    ['Status', /Closed.*11 conversations/],
  ])('shows conversation counts in the %s filter', (label, expected) => {
    renderPanel();

    fireEvent.click(screen.getByRole('button', { name: label }));
    const list = screen.getByRole('listbox', { name: label });

    expect(within(list).getByLabelText(expected)).toBeInTheDocument();
  });

  it('counts annotations rather than conversations on the report tab', () => {
    // Most conversations are never reviewed, so a conversation tally beside a
    // report of annotations would overstate what the report will show.
    renderPanel({ countMode: 'reviews' });

    fireEvent.click(screen.getByRole('button', { name: 'Agent' }));
    const list = screen.getByRole('listbox', { name: 'Agent' });

    expect(within(list).getByLabelText(/Ada L\..*2 annotations/)).toBeInTheDocument();
    expect(within(list).getByLabelText(/Unassigned.*1 annotations/)).toBeInTheDocument();
    expect(within(list).queryByLabelText(/Ada L\..*5 /)).not.toBeInTheDocument();
  });

  it('shows the date-basis toggle only on the report tab', () => {
    const { onChange } = renderPanel({ showDateBasis: true });

    const select = screen.getByLabelText('Date range applies to');
    fireEvent.change(select, { target: { value: 'conversation' } });

    expect(onChange).toHaveBeenCalledWith({ date_basis: 'conversation' });
  });

  it('hides the date-basis toggle on the conversations tab', () => {
    renderPanel({ showDateBasis: false });

    expect(screen.queryByLabelText('Date range applies to')).not.toBeInTheDocument();
  });

  it('disables Clear all until something is filtered', () => {
    const { onClear } = renderPanel({ activeFilterCount: 0 });
    expect(screen.getByRole('button', { name: /clear all/i })).toBeDisabled();
    expect(onClear).not.toHaveBeenCalled();
  });

  it('clears every filter when asked', () => {
    const { onClear } = renderPanel({ activeFilterCount: 3 });

    expect(screen.getByText('3 filters applied')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /clear all/i }));
    expect(onClear).toHaveBeenCalled();
  });
});
