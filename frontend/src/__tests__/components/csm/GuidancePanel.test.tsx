import React from 'react';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import '@testing-library/jest-dom';
import { GuidancePanel } from '@/components/csm/conversations/GuidancePanel';
import CsmGuidanceAPI from '@/lib/api/csmGuidanceApi';
import { useCsmConversationStore } from '@/lib/csmConversationStore';
import type { ConversationGuidance, WorkspaceGuidanceEntry } from '@/types/csmGuidance';

jest.mock('@/lib/api/csmGuidanceApi', () => ({
  __esModule: true,
  default: { forConversation: jest.fn() },
}));

const mockedForConversation = CsmGuidanceAPI.forConversation as jest.Mock;

function makeEntry(overrides: Partial<WorkspaceGuidanceEntry>): WorkspaceGuidanceEntry {
  return {
    id: 1,
    guidance_type: 'suggested_reply',
    guidance_type_display: 'Suggested Reply',
    trigger_description: 'Customer asks about refunds',
    recommended_response: 'Refunds take 5 business days.',
    display_order: 0,
    ...overrides,
  };
}

function makeGuidance(entries: WorkspaceGuidanceEntry[]): ConversationGuidance {
  return { experience_group: { id: 11, name: 'VIP Support' }, entries };
}

describe('GuidancePanel', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    useCsmConversationStore.setState({ guidanceVersionByGroup: {}, pendingComposerInsert: null });
  });

  it('lists entries in the order returned for the matched group', async () => {
    mockedForConversation.mockResolvedValue(
      makeGuidance([
        makeEntry({ id: 2, trigger_description: 'First trigger', guidance_type_display: 'Handoff' }),
        makeEntry({ id: 1, trigger_description: 'Second trigger' }),
      ])
    );

    render(<GuidancePanel conversationId={42} canInsert />);

    const list = await screen.findByRole('list', { name: 'Guidance entries' });
    const items = within(list).getAllByRole('listitem');
    expect(items[0]).toHaveTextContent('First trigger');
    expect(items[0]).toHaveTextContent('Handoff');
    expect(items[1]).toHaveTextContent('Second trigger');
    expect(screen.getByText('VIP Support')).toBeInTheDocument();
    expect(mockedForConversation).toHaveBeenCalledWith(42);
  });

  it('expands an entry and inserts its response into the composer with one click', async () => {
    mockedForConversation.mockResolvedValue(makeGuidance([makeEntry({})]));
    render(<GuidancePanel conversationId={42} canInsert />);

    const toggle = await screen.findByRole('button', { name: /Customer asks about refunds/ });
    expect(screen.queryByText('Refunds take 5 business days.')).not.toBeInTheDocument();
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByText('Refunds take 5 business days.')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /Insert into reply/ }));

    expect(useCsmConversationStore.getState().pendingComposerInsert).toMatchObject({
      conversationId: 42,
      text: 'Refunds take 5 business days.',
    });
  });

  it('disables insert while the conversation is unclaimed', async () => {
    mockedForConversation.mockResolvedValue(makeGuidance([makeEntry({})]));
    render(<GuidancePanel conversationId={42} canInsert={false} />);

    fireEvent.click(await screen.findByRole('button', { name: /Customer asks about refunds/ }));

    expect(screen.getByRole('button', { name: /Insert into reply/ })).toBeDisabled();
    expect(screen.getByText('Claim this conversation to reply.')).toBeInTheDocument();
  });

  it('refetches when a live update arrives for its group', async () => {
    mockedForConversation.mockResolvedValueOnce(
      makeGuidance([makeEntry({ id: 1, trigger_description: 'Old trigger' })])
    );
    render(<GuidancePanel conversationId={42} canInsert />);
    await screen.findByText('Old trigger');

    mockedForConversation.mockResolvedValueOnce(makeGuidance([]));
    act(() => {
      useCsmConversationStore.getState().bumpGuidance([11]);
    });

    await waitFor(() => expect(mockedForConversation).toHaveBeenCalledTimes(2));
    expect(await screen.findByText(/No guidance has been configured/)).toBeInTheDocument();
    expect(screen.queryByText('Old trigger')).not.toBeInTheDocument();
  });

  it('ignores live updates for other groups', async () => {
    mockedForConversation.mockResolvedValue(makeGuidance([makeEntry({})]));
    render(<GuidancePanel conversationId={42} canInsert />);
    await screen.findByText('Customer asks about refunds');

    act(() => {
      useCsmConversationStore.getState().bumpGuidance([99]);
    });

    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(mockedForConversation).toHaveBeenCalledTimes(1);
  });

  it('explains when the customer has no experience group', async () => {
    mockedForConversation.mockResolvedValue({ experience_group: null, entries: [] });
    render(<GuidancePanel conversationId={42} canInsert />);

    expect(await screen.findByText(/not in an Experience Group/)).toBeInTheDocument();
  });
});
