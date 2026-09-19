import React from 'react';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom';
import GuidanceFormModal from '@/components/csm-settings/GuidanceFormModal';
import CsmGuidanceAPI from '@/lib/api/csmGuidanceApi';
import type { GuidanceEntry } from '@/types/csmGuidance';
import type { ExperienceGroupListItem } from '@/types/experienceGroup';

jest.mock('@/lib/api/csmGuidanceApi', () => ({
  __esModule: true,
  default: { create: jest.fn(), update: jest.fn() },
}));

const mockedCreate = CsmGuidanceAPI.create as jest.Mock;
const mockedUpdate = CsmGuidanceAPI.update as jest.Mock;

const groups = [
  { id: 10, name: 'VIP Support' },
  { id: 11, name: 'Standard' },
] as ExperienceGroupListItem[];

function makeEntry(overrides: Partial<GuidanceEntry> = {}): GuidanceEntry {
  return {
    id: 3,
    project: 1,
    guidance_type: 'handoff',
    guidance_type_display: 'Handoff',
    trigger_description: 'Customer wants a manager',
    recommended_response: 'Transfer to the T2 queue.',
    experience_groups: [{ id: 11, name: 'Standard', display_order: 0 }],
    created_at: '2026-09-20T00:00:00Z',
    updated_at: '2026-09-20T00:00:00Z',
    ...overrides,
  };
}

async function selectGroup(name: string) {
  // The native type <select> is also a combobox, so target the multi-select by id.
  fireEvent.click(document.getElementById('guidance-groups')!);
  await waitFor(() => expect(screen.getByRole('option', { name })).toBeInTheDocument());
  fireEvent.click(screen.getByRole('option', { name }));
}

describe('GuidanceFormModal', () => {
  const onClose = jest.fn();
  const onSaved = jest.fn();

  beforeEach(() => {
    jest.clearAllMocks();
  });

  const renderModal = (props: Partial<React.ComponentProps<typeof GuidanceFormModal>> = {}) =>
    render(
      <GuidanceFormModal
        isOpen
        projectId={1}
        editing={null}
        experienceGroups={groups}
        defaultExperienceGroupId={null}
        onClose={onClose}
        onSaved={onSaved}
        {...props}
      />,
    );

  it('offers all four guidance types', () => {
    renderModal();
    const options = Array.from(
      (screen.getByLabelText(/^type/i) as HTMLSelectElement).options,
    ).map((o) => o.textContent);
    expect(options).toEqual(['Handoff', 'Suggested Reply', 'Escalation Procedure', 'Process Note']);
  });

  it('requires trigger, response and at least one group before submitting', async () => {
    renderModal();
    fireEvent.click(screen.getByRole('button', { name: 'Create' }));

    expect(await screen.findByText('Describe when this guidance applies.')).toBeInTheDocument();
    expect(screen.getByText('Enter the recommended response or action.')).toBeInTheDocument();
    expect(screen.getByText('Select at least one experience group.')).toBeInTheDocument();
    expect(mockedCreate).not.toHaveBeenCalled();
  });

  it('creates an entry linked to the selected groups', async () => {
    const saved = makeEntry();
    mockedCreate.mockResolvedValue(saved);
    renderModal({ defaultExperienceGroupId: 10 });

    fireEvent.change(screen.getByLabelText(/^type/i), { target: { value: 'escalation_procedure' } });
    fireEvent.change(screen.getByLabelText(/trigger description/i), {
      target: { value: '  Threatens chargeback  ' },
    });
    fireEvent.change(screen.getByLabelText(/recommended response/i), {
      target: { value: 'Escalate to billing lead.' },
    });
    await selectGroup('Standard');
    fireEvent.click(screen.getByRole('button', { name: 'Create' }));

    await waitFor(() => expect(onSaved).toHaveBeenCalledWith(saved));
    expect(mockedCreate).toHaveBeenCalledWith(1, {
      guidance_type: 'escalation_procedure',
      trigger_description: 'Threatens chargeback',
      recommended_response: 'Escalate to billing lead.',
      experience_group_ids: [10, 11],
    });
  });

  it('prefills and updates an existing entry', async () => {
    const entry = makeEntry();
    mockedUpdate.mockResolvedValue(entry);
    renderModal({ editing: entry });

    expect(screen.getByLabelText(/trigger description/i)).toHaveValue('Customer wants a manager');
    fireEvent.click(screen.getByRole('button', { name: 'Save changes' }));

    await waitFor(() => expect(mockedUpdate).toHaveBeenCalledWith(3, {
      guidance_type: 'handoff',
      trigger_description: 'Customer wants a manager',
      recommended_response: 'Transfer to the T2 queue.',
      experience_group_ids: [11],
    }));
  });

  it('shows server field errors', async () => {
    mockedCreate.mockRejectedValue({
      response: { data: { experience_group_ids: ['Experience group 10 is not in this project.'] } },
    });
    renderModal({ defaultExperienceGroupId: 10 });
    fireEvent.change(screen.getByLabelText(/trigger description/i), { target: { value: 'x' } });
    fireEvent.change(screen.getByLabelText(/recommended response/i), { target: { value: 'y' } });
    fireEvent.click(screen.getByRole('button', { name: 'Create' }));

    expect(
      await screen.findByText('Experience group 10 is not in this project.'),
    ).toBeInTheDocument();
    expect(onSaved).not.toHaveBeenCalled();
  });
});
