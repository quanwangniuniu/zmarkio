import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import DecisionTreeDetailPanel from '@/components/decisions/DecisionTreeDetailPanel';

const mockSaveDecision = jest.fn();
const mockDraft = {
  id: 1,
  createdBy: 100,
  title: 'Saved title',
  confidenceScore: 3,
  options: [{ id: 1, text: 'Existing option', isSelected: true, order: 4 }],
};

jest.mock('@/lib/authStore', () => ({
  useAuthStore: (selector: (state: { user: { id: number } }) => unknown) =>
    selector({ user: { id: 100 } }),
}));
jest.mock('@/lib/buildUrl', () => ({
  useBuildUrl: () => (path: string) => path,
}));
jest.mock('@/components/decisions/detail/hooks/useDecisionDetail', () => ({
  useDecisionDetail: () => ({
    base: mockDraft,
    draft: mockDraft,
    status: 'DRAFT',
    signals: [],
    saveDecision: mockSaveDecision,
  }),
}));
jest.mock('@/components/decisions/detail/hooks/useProjectRole', () => ({
  useProjectRole: () => ({ canEdit: true, members: [] }),
}));
jest.mock('@/components/decisions/detail/DecisionConnectionsAside', () => () => null);
jest.mock('@/components/decisions/detail/DecisionLinkedTasksSection', () => () => null);

function openEditor() {
  render(<DecisionTreeDetailPanel decisionId={1} projectId={1} canEdit onClose={jest.fn()} />);
  fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
}

beforeEach(() => {
  mockSaveDecision.mockReset().mockResolvedValue(mockDraft);
});

it('blocks a long title before saving and clears its error when editing again', () => {
  openEditor();
  fireEvent.change(screen.getByRole('textbox', { name: 'Decision title' }), {
    target: { value: 'a'.repeat(256) },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Save' }));

  expect(screen.getByRole('alert')).toHaveTextContent('Title must be 255 characters or fewer.');
  expect(mockSaveDecision).not.toHaveBeenCalled();

  fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
  fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
  expect(screen.queryByRole('alert')).not.toBeInTheDocument();
});

it('validates a newly added option even if it has not received focus', async () => {
  openEditor();
  fireEvent.click(screen.getByRole('button', { name: 'Add option' }));
  const newOption = screen.getByPlaceholderText('Option 2');
  fireEvent.click(screen.getByRole('button', { name: 'Save' }));

  expect(screen.getByText('Option 2: enter option text.')).toBeInTheDocument();
  expect(mockSaveDecision).not.toHaveBeenCalled();

  fireEvent.change(newOption, { target: { value: 'New option' } });
  fireEvent.blur(newOption);
  fireEvent.click(screen.getByRole('button', { name: 'Save' }));

  await waitFor(() => {
    expect(mockSaveDecision).toHaveBeenCalledWith(expect.objectContaining({
      options: [
        expect.objectContaining({ text: 'Existing option', order: 0 }),
        expect.objectContaining({ text: 'New option', order: 1 }),
      ],
    }));
  });
});

it('shows API field errors and leaves the panel open for editing', async () => {
  mockSaveDecision.mockRejectedValueOnce({
    isAxiosError: true,
    response: {
      status: 400,
      data: {
        title: ['Title rejected by the API.'],
        confidenceScore: ['Confidence rejected by the API.'],
        options: ['Options rejected by the API.'],
      },
    },
  });

  openEditor();
  fireEvent.click(screen.getByRole('button', { name: 'Save' }));

  expect(await screen.findByText('Title rejected by the API.')).toBeInTheDocument();
  expect(screen.getByText('Confidence rejected by the API.')).toBeInTheDocument();
  expect(screen.getByText('Options rejected by the API.')).toBeInTheDocument();
  expect(screen.getByRole('textbox', { name: 'Decision title' })).toBeInTheDocument();
});
