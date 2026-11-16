import React from 'react';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom';
import SystemConfigPage from '@/app/(project)/system-config/page';
import { fetchSystemConfig, saveSystemConfig } from '@/lib/api/orgCustomizationApi';
import type { SystemConfig } from '@/types/orgCustomization';

jest.mock('@/lib/api/orgCustomizationApi', () => ({
  fetchSystemConfig: jest.fn(),
  saveSystemConfig: jest.fn(),
}));

jest.mock('react-hot-toast', () => ({
  __esModule: true,
  default: { success: jest.fn(), error: jest.fn() },
}));

jest.mock('@/components/auth/ProtectedRoute', () => ({
  ProtectedRoute: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));

jest.mock('@/components/dashboard/DashboardLayout', () => ({
  __esModule: true,
  default: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));

jest.mock('@/lib/projectStore', () => ({
  useProjectStore: (selector: (state: unknown) => unknown) =>
    selector({ activeProject: { id: 1, name: 'Acme' } }),
}));

const mockedFetch = fetchSystemConfig as jest.Mock;
const mockedSave = saveSystemConfig as jest.Mock;

const config: SystemConfig = {
  task: {
    task_types: [
      { key: 'budget', default_label: 'Budget', label: null, short_label: null, color: null, enabled: true, order: 0 },
    ],
    task_statuses: [
      { key: 'TODO', default_label: 'To Do', label: 'Backlog', default_category: 'todo', category: null },
    ],
    task_priorities: [{ key: 'HIGH', default_label: 'High', label: null }],
    task_field_options: [],
    status_categories: [{ key: 'todo', label: 'To Do' }],
  },
  csm: {
    options: [{
      field: 'ticket_priority', value: 'low', default_label: 'Low', label: null, color: null,
      enabled: true, order: 0, is_default: false, editable: ['label'],
    }],
  },
  can_edit: true,
};

const labelInputs = () => screen.getAllByRole('textbox') as HTMLInputElement[];

beforeEach(() => {
  mockedFetch.mockResolvedValue(config);
  mockedSave.mockResolvedValue(config);
});

it('prefills labels with defaults and saves unchanged defaults as no override', async () => {
  render(<SystemConfigPage />);
  await screen.findByText('Task Types');

  const values = labelInputs().map((input) => input.value);
  expect(values).toEqual(expect.arrayContaining(['Budget', 'Backlog', 'High', 'Low']));
  expect(values).not.toContain('');

  const [budgetLabel] = labelInputs();
  fireEvent.change(budgetLabel, { target: { value: 'Spend' } });
  fireEvent.change(budgetLabel, { target: { value: 'Budget' } });
  fireEvent.click(screen.getByRole('button', { name: 'Save' }));

  await waitFor(() => expect(mockedSave).toHaveBeenCalled());
  const payload = mockedSave.mock.calls[0][1];
  expect(payload.task.task_types[0].label).toBeNull();
  expect(payload.task.task_statuses[0].label).toBe('Backlog');
  expect(payload.csm.options[0].label).toBeNull();
});
