import React from 'react';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom';
import toast from 'react-hot-toast';
import GuidanceSettingsPage from '@/app/(project)/admin/csm/settings/guidance/page';
import CsmGuidanceAPI from '@/lib/api/csmGuidanceApi';
import { ExperienceGroupAPI } from '@/lib/api/experienceGroupApi';
import type { GuidanceEntry } from '@/types/csmGuidance';

jest.mock('@/lib/api/csmGuidanceApi', () => ({
  __esModule: true,
  default: { list: jest.fn(), remove: jest.fn(), capabilities: jest.fn() },
}));

jest.mock('@/lib/api/experienceGroupApi', () => ({
  __esModule: true,
  ExperienceGroupAPI: { list: jest.fn() },
}));

jest.mock('react-hot-toast', () => ({
  __esModule: true,
  default: { success: jest.fn(), error: jest.fn() },
}));

jest.mock('@/components/csm-settings/useProjectIdFromUrl', () => ({
  useProjectIdFromUrl: () => ({ projectId: 1, projectValid: true }),
}));

// The form is exercised by its own test; stub it so this file stays on the page.
jest.mock('@/components/csm-settings/GuidanceFormModal', () => ({
  __esModule: true,
  default: () => null,
}));

const mockedList = CsmGuidanceAPI.list as jest.Mock;
const mockedRemove = CsmGuidanceAPI.remove as jest.Mock;
const mockedCapabilities = CsmGuidanceAPI.capabilities as jest.Mock;
const mockedGroups = ExperienceGroupAPI.list as jest.Mock;

const entry: GuidanceEntry = {
  id: 7,
  project: 1,
  guidance_type: 'handoff',
  guidance_type_display: 'Handoff',
  trigger_description: 'Customer mentions a legal threat',
  recommended_response: 'Hand off to the T3 Escalations queue.',
  experience_groups: [{ id: 10, name: 'VIP Support', display_order: 0 }],
  created_at: '2026-09-20T00:00:00Z',
  updated_at: '2026-09-20T00:00:00Z',
};

async function renderPage() {
  render(<GuidanceSettingsPage />);
  await screen.findByText(entry.trigger_description);
}

const openDeleteDialog = () =>
  fireEvent.click(screen.getByRole('button', { name: 'Delete guidance' }));

describe('GuidanceSettingsPage delete confirmation', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    mockedGroups.mockResolvedValue({ data: [{ id: 10, name: 'VIP Support' }] });
    mockedCapabilities.mockResolvedValue({ can_manage: true });
    mockedList.mockResolvedValue([entry]);
    mockedRemove.mockResolvedValue(undefined);
  });

  it('asks in an in-page dialog rather than a native confirm', async () => {
    const nativeConfirm = jest.spyOn(window, 'confirm');
    await renderPage();

    openDeleteDialog();

    const dialog = await screen.findByRole('dialog');
    expect(dialog).toHaveTextContent('Delete guidance');
    expect(dialog).toHaveTextContent(entry.trigger_description);
    expect(nativeConfirm).not.toHaveBeenCalled();
    expect(mockedRemove).not.toHaveBeenCalled();
    nativeConfirm.mockRestore();
  });

  it('keeps the entry when the dialog is cancelled', async () => {
    await renderPage();
    openDeleteDialog();
    await screen.findByRole('dialog');

    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));

    expect(mockedRemove).not.toHaveBeenCalled();
    expect(screen.getByText(entry.trigger_description)).toBeInTheDocument();
  });

  it('removes the entry and reports success when confirmed', async () => {
    await renderPage();
    openDeleteDialog();
    await screen.findByRole('dialog');

    fireEvent.click(screen.getByRole('button', { name: 'Delete' }));

    await waitFor(() => expect(mockedRemove).toHaveBeenCalledWith(entry.id));
    await waitFor(() =>
      expect(screen.queryByText(entry.trigger_description)).not.toBeInTheDocument(),
    );
    expect(toast.success).toHaveBeenCalledWith('Guidance deleted.');
  });

  it('keeps the entry listed and reports the failure when the delete fails', async () => {
    mockedRemove.mockRejectedValue(new Error('boom'));
    await renderPage();
    openDeleteDialog();
    await screen.findByRole('dialog');

    fireEvent.click(screen.getByRole('button', { name: 'Delete' }));

    await waitFor(() => expect(toast.error).toHaveBeenCalledWith('Could not delete guidance.'));
    expect(screen.getByText(entry.trigger_description)).toBeInTheDocument();
  });

  it('truncates a long trigger in the dialog message', async () => {
    const long = 'x'.repeat(400);
    mockedList.mockResolvedValue([{ ...entry, trigger_description: long }]);
    render(<GuidanceSettingsPage />);
    await screen.findByText(long);

    openDeleteDialog();

    const dialog = await screen.findByRole('dialog');
    expect(dialog).toHaveTextContent(`Delete "${'x'.repeat(120)}…"`);
  });
});
