import React from 'react';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom';
import { TemplatePicker } from '@/components/csm/conversations/ConversationComposer';
import { QuickReplyTemplateAPI } from '@/lib/api/csmConversationApi';
import type { QuickReplyTemplate } from '@/types/csmConversation';

jest.mock('@/lib/api/csmConversationApi', () => ({
  __esModule: true,
  default: { sendMessage: jest.fn() },
  QuickReplyTemplateAPI: { list: jest.fn() },
}));

const mockedList = QuickReplyTemplateAPI.list as jest.Mock;

function makeTemplate(overrides: Partial<QuickReplyTemplate>): QuickReplyTemplate {
  return {
    id: 1,
    slug: 'greeting',
    organisation: 5,
    team: null,
    title: 'Greeting',
    content: 'Hello there, how can I help?',
    rich_body: null,
    tags: ['greeting'],
    is_active: true,
    created_by: 1,
    created_by_name: 'Agent',
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    ...overrides,
  };
}

describe('TemplatePicker', () => {
  const onSelect = jest.fn();
  const onClose = jest.fn();

  beforeEach(() => {
    jest.clearAllMocks();
  });

  const renderPicker = () =>
    render(<TemplatePicker organisationId={5} onSelect={onSelect} onClose={onClose} />);

  test('shows loading state then renders fetched templates', async () => {
    mockedList.mockResolvedValue([makeTemplate({})]);
    renderPicker();

    expect(screen.getByText('Loading…')).toBeInTheDocument();
    expect(await screen.findByText('Greeting')).toBeInTheDocument();
    expect(mockedList).toHaveBeenCalledWith({ organisation: 5 });
  });

  test('shows empty-state message when there are no templates at all', async () => {
    mockedList.mockResolvedValue([]);
    renderPicker();

    expect(await screen.findByText(/No quick reply templates yet/i)).toBeInTheDocument();
  });

  test('filters by keyword search across title and content', async () => {
    mockedList.mockResolvedValue([
      makeTemplate({ id: 1, title: 'Greeting', content: 'Hello there' }),
      makeTemplate({ id: 2, title: 'Refund policy', content: 'Refunds take 5 days' }),
    ]);
    renderPicker();
    await screen.findByText('Greeting');

    fireEvent.change(screen.getByPlaceholderText('Search…'), { target: { value: 'refund' } });

    expect(screen.queryByText('Greeting')).not.toBeInTheDocument();
    expect(screen.getByText('Refund policy')).toBeInTheDocument();
  });

  test('shows "No matches" when a search excludes every template', async () => {
    mockedList.mockResolvedValue([makeTemplate({})]);
    renderPicker();
    await screen.findByText('Greeting');

    fireEvent.change(screen.getByPlaceholderText('Search…'), { target: { value: 'nonexistent' } });

    expect(await screen.findByText('No templates found')).toBeInTheDocument();
  });

  test('filters by tag using the tag select', async () => {
    mockedList.mockResolvedValue([
      makeTemplate({ id: 1, title: 'Greeting', tags: ['greeting'] }),
      makeTemplate({ id: 2, title: 'Refund policy', tags: ['billing'] }),
    ]);
    renderPicker();
    await screen.findByText('Greeting');

    fireEvent.change(screen.getByDisplayValue('All tags'), { target: { value: 'billing' } });

    expect(screen.queryByText('Greeting')).not.toBeInTheDocument();
    expect(screen.getByText('Refund policy')).toBeInTheDocument();
  });

  test('clicking a template row inserts it in a single action, without requiring preview', async () => {
    const template = makeTemplate({});
    mockedList.mockResolvedValue([template]);
    renderPicker();

    const row = await screen.findByText('Greeting');
    fireEvent.click(row.closest('button')!);

    expect(onSelect).toHaveBeenCalledTimes(1);
    expect(onSelect).toHaveBeenCalledWith(template);
  });

  test('Preview shows the full body and tags before inserting', async () => {
    const template = makeTemplate({ content: 'Full preview body text' });
    mockedList.mockResolvedValue([template]);
    renderPicker();
    await screen.findByText('Greeting');

    fireEvent.click(screen.getByText('Preview'));

    expect(screen.getByText('Preview: Greeting')).toBeInTheDocument();
    expect(screen.getByText('Full preview body text')).toBeInTheDocument();
    expect(onSelect).not.toHaveBeenCalled();

    fireEvent.click(screen.getByText('Insert'));
    expect(onSelect).toHaveBeenCalledWith(template);
  });

  test('Back from preview returns to the list without inserting', async () => {
    mockedList.mockResolvedValue([makeTemplate({})]);
    renderPicker();
    await screen.findByText('Greeting');

    fireEvent.click(screen.getByText('Preview'));
    expect(await screen.findByText('Preview: Greeting')).toBeInTheDocument();

    fireEvent.click(screen.getByText('← Back'));

    await waitFor(() => expect(screen.queryByText('Preview: Greeting')).not.toBeInTheDocument());
    expect(onSelect).not.toHaveBeenCalled();
  });

  test('close button calls onClose', async () => {
    mockedList.mockResolvedValue([makeTemplate({})]);
    const { container } = renderPicker();
    await screen.findByText('Greeting');

    fireEvent.click(container.querySelector('button.p-0\\.5')!);
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});

describe('TemplatePicker (sandbox preview mode)', () => {
  beforeEach(() => jest.clearAllMocks());

  test('lists templates as the chosen team and hides the close button', async () => {
    mockedList.mockResolvedValue([makeTemplate({})]);
    render(<TemplatePicker organisationId={5} viewAsTeam={12} onSelect={jest.fn()} />);

    expect(await screen.findByText('Greeting')).toBeInTheDocument();
    expect(mockedList).toHaveBeenCalledWith({ organisation: 5, view_as_team: 12 });
    expect(screen.queryByRole('button', { name: '' })).not.toBeInTheDocument();
  });

  test('uses the custom insert label in the preview', async () => {
    const onSelect = jest.fn();
    mockedList.mockResolvedValue([makeTemplate({})]);
    render(
      <TemplatePicker organisationId={5} viewAsTeam="none" insertLabel="Insert as preview" onSelect={onSelect} />,
    );
    fireEvent.click(await screen.findByText('Preview'));
    fireEvent.click(screen.getByRole('button', { name: 'Insert as preview' }));
    expect(onSelect).toHaveBeenCalledWith(expect.objectContaining({ id: 1 }));
  });
});
