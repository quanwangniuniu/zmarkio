import React from 'react';
import { render, screen, fireEvent, within } from '@testing-library/react';
import '@testing-library/jest-dom';

import QualityFiltersPanel from '@/components/csm/quality/QualityFiltersPanel';
import { EMPTY_QUALITY_FILTERS, QualityFilterOptions } from '@/types/csmQuality';

const options: QualityFilterOptions = {
  organisations: [{ id: 1, name: 'Acme' }],
  queues: [
    { id: 3, name: 'T1 Frontline', organisation: 1, is_active: true },
    { id: 4, name: 'Retired', organisation: 1, is_active: false },
  ],
  agents: [{ user_id: 9, name: 'Ada L.', email: 'ada@x.io' }],
  channels: [
    { value: 'web', label: 'Web' },
    { value: 'email', label: 'Email' },
  ],
  statuses: [
    { value: 'closed', label: 'Closed' },
    { value: 'active', label: 'Active' },
  ],
  tags: ['vip', 'refund'],
  customers: [{ id: 5, name: 'Grace H.', email: 'grace@x.io' }],
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

    fireEvent.click(within(list).getByLabelText('Ada L.'));
    expect(onChange).toHaveBeenCalledWith({ agent: [9] });

    fireEvent.click(within(list).getByLabelText('Unassigned'));
    expect(onChange).toHaveBeenCalledWith({ agent: ['unassigned'] });
  });

  it('emits queue ids and marks archived queues', () => {
    const { onChange } = renderPanel();

    fireEvent.click(screen.getByRole('button', { name: 'Queue' }));
    const list = screen.getByRole('listbox', { name: 'Queue' });

    expect(within(list).getByLabelText('Retired (archived)')).toBeInTheDocument();
    fireEvent.click(within(list).getByLabelText('T1 Frontline'));
    expect(onChange).toHaveBeenCalledWith({ queue: [3] });
  });

  it('emits channel, customer, tag and status values', () => {
    const { onChange } = renderPanel();

    fireEvent.click(screen.getByRole('button', { name: 'Channel' }));
    fireEvent.click(within(screen.getByRole('listbox', { name: 'Channel' })).getByLabelText('Email'));
    expect(onChange).toHaveBeenCalledWith({ channel: ['email'] });

    fireEvent.click(screen.getByRole('button', { name: 'Customer' }));
    fireEvent.click(within(screen.getByRole('listbox', { name: 'Customer' })).getByLabelText('Grace H.'));
    expect(onChange).toHaveBeenCalledWith({ customer: [5] });

    fireEvent.click(screen.getByRole('button', { name: 'Tag' }));
    fireEvent.click(within(screen.getByRole('listbox', { name: 'Tag' })).getByLabelText('vip'));
    expect(onChange).toHaveBeenCalledWith({ tag: ['vip'] });

    fireEvent.click(screen.getByRole('button', { name: 'Status' }));
    fireEvent.click(within(screen.getByRole('listbox', { name: 'Status' })).getByLabelText('Closed'));
    expect(onChange).toHaveBeenCalledWith({ status: ['closed'] });
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
