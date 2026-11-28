import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import SignalsPanel from '@/components/decisions/SignalsPanel';
import { DecisionAPI } from '@/lib/api/decisionApi';

jest.mock('@/lib/api/decisionApi', () => ({
  DecisionAPI: {
    listSignals: jest.fn(),
    updateSignal: jest.fn(),
  },
}));

jest.mock('react-hot-toast', () => ({
  __esModule: true,
  default: { success: jest.fn(), error: jest.fn() },
}));

const signal = {
  id: 1,
  decisionId: 2,
  metric: 'ROAS',
  movement: 'NO_SIGNIFICANT_CHANGE',
  period: 'LAST_14_DAYS',
  comparison: 'NONE',
  scopeType: 'CAMPAIGN',
  scopeValue: 'Test campaign',
  deltaValue: 12,
  deltaUnit: 'PERCENT',
  displayText: 'Test override',
  displayTextOverride: 'Test override',
};

async function openEditor() {
  render(<SignalsPanel decisionId={2} projectId={3} />);
  fireEvent.click(await screen.findByRole('button', { name: /^Edit$/ }));
}

beforeEach(() => {
  jest.clearAllMocks();
  (DecisionAPI.listSignals as jest.Mock).mockResolvedValue({ items: [signal] });
  (DecisionAPI.updateSignal as jest.Mock).mockResolvedValue(signal);
});

test('sends null for cleared scope, delta, and custom description', async () => {
  await openEditor();

  fireEvent.change(screen.getByDisplayValue('Campaign'), { target: { value: '' } });
  fireEvent.change(screen.getByDisplayValue('Test campaign'), { target: { value: '' } });
  fireEvent.change(screen.getByPlaceholderText('Value'), { target: { value: '' } });
  fireEvent.change(screen.getByDisplayValue('Percent'), { target: { value: '' } });
  fireEvent.click(screen.getByRole('button', { name: 'Reset to auto text' }));
  fireEvent.click(screen.getByRole('button', { name: 'Update Signal' }));

  await waitFor(() => {
    expect(DecisionAPI.updateSignal).toHaveBeenCalledWith(
      2,
      1,
      expect.objectContaining({
        scopeType: null,
        scopeValue: null,
        deltaValue: null,
        deltaUnit: null,
        displayTextOverride: null,
      }),
      3,
    );
  });
});

test('sends delta zero as a number', async () => {
  await openEditor();

  fireEvent.change(screen.getByPlaceholderText('Value'), { target: { value: '0' } });
  fireEvent.click(screen.getByRole('button', { name: 'Update Signal' }));

  await waitFor(() => {
    expect(DecisionAPI.updateSignal).toHaveBeenCalledWith(
      2,
      1,
      expect.objectContaining({ deltaValue: 0, deltaUnit: 'PERCENT' }),
      3,
    );
  });
});