import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import toast from 'react-hot-toast';
import VariationsStudioPage from '@/app/(project)/variations-studio/page';
import api from '@/lib/api';

jest.mock('@/components/auth/ProtectedRoute', () => ({
  ProtectedRoute: ({ children }: React.PropsWithChildren) => children,
}));
jest.mock('@/components/dashboard/DashboardLayout', () => ({
  __esModule: true,
  default: ({ children }: React.PropsWithChildren) => children,
}));
jest.mock('@/lib/buildUrl', () => ({ useBuildUrl: () => (path: string) => path }));
jest.mock('@/lib/projectStore', () => ({
  useProjectStore: (select: (state: { activeProject: { id: number }; hasHydrated: boolean }) => unknown) =>
    select({ activeProject: { id: 1 }, hasHydrated: true }),
}));
jest.mock('react-hot-toast', () => ({ __esModule: true, default: { error: jest.fn() } }));

const failedBatch = {
  batch_id: 'batch-1', count_requested: 2, count_succeeded: 0, count_failed: 2,
  results: [], failed_indices: [0, 1],
};
const headlineFailure = {
  index: 1, violations: [{ field: 'headline', rule: 'max_chars', actual: 43, limit: 40 }],
};

async function generate(status: 200 | 502, data: unknown) {
  jest.spyOn(api, 'post').mockImplementation(async url => {
    if (url === '/api/ad_copy_variation/initialize/') return { data: {} };
    if (url !== '/api/ad_copy_variation/variations/generate/') throw new Error(`Unexpected POST ${url}`);
    const response = { data, status };
    if (status === 502) return Promise.reject({ isAxiosError: true, response });
    return response;
  });

  const { container } = render(<VariationsStudioPage />);
  fireEvent.change(screen.getByLabelText('Custom count'), { target: { value: '2' } });
  fireEvent.click(screen.getByRole('button', { name: 'Generate 2 variations' }));
  await screen.findByRole('button', { name: 'Generate 2 variations' });
  return container;
}

afterEach(() => {
  jest.restoreAllMocks();
  jest.clearAllMocks();
});

it('shows the original failed item number beside a saved card on partial success', async () => {
  const container = await generate(200, {
    ...failedBatch, count_succeeded: 1, count_failed: 1, failed_indices: [1],
    results: [{
      id: 7, status: 'draft', source_mode: 'custom', batch_id: 'batch-1',
      hook: 'Saved hook', headline: 'Saved headline', description: 'Saved description', cta: 'SHOP_NOW',
    }],
    field_failures: [headlineFailure],
  });

  expect(screen.getByRole('article')).toHaveTextContent('Saved headline');
  expect(container).toHaveTextContent('1 of 2 succeeded. 1 failed. Click “Generate 2 more” to retry.');
  expect(container).toHaveTextContent(/Item 2 in this batch was not saved.*headline: 43 characters \(maximum 40\)/i);
});

it('shows each failed item and both hook limits on a 502 without cards', async () => {
  await generate(502, {
    ...failedBatch,
    field_failures: [{
      index: 0,
      violations: [
        { field: 'hook', rule: 'max_words', actual: 11, limit: 10 },
        { field: 'hook', rule: 'max_chars', actual: 54, limit: 50 },
      ],
    }, headlineFailure],
  });

  const first = screen.getByText('Item 1 in this batch was not saved').closest('li');
  expect(first).toHaveTextContent('hook: 11 words (maximum 10)');
  expect(first).toHaveTextContent('hook: 54 characters (maximum 50)');
  expect(screen.getByText('Item 2 in this batch was not saved').closest('li')).toHaveTextContent('headline: 43 characters (maximum 40)');
  expect(screen.queryByRole('article')).not.toBeInTheDocument();
  expect(screen.queryByText(/more” to retry/)).not.toBeInTheDocument();
});

it('shows the provider error alongside a field failure on a mixed 502', async () => {
  const container = await generate(502, {
    ...failedBatch, error: 'Ollama disconnected', field_failures: [headlineFailure],
  });

  expect(screen.getByText('Ollama disconnected')).toBeInTheDocument();
  expect(container).toHaveTextContent(/Item 2 in this batch was not saved.*headline: 43 characters \(maximum 40\)/i);
});

it('shows the readable details for an item with an unfamiliar rule', async () => {
  await generate(502, {
    ...failedBatch,
    field_failures: [{
      ...headlineFailure,
      violations: [
        ...headlineFailure.violations,
        null,
        { field: 'headline', rule: 'max_bytes', actual: 84, limit: 80 },
      ],
    }],
  });

  const item = screen.getByText('Item 2 in this batch was not saved').closest('li');
  expect(item).toHaveTextContent('headline: 43 characters (maximum 40)');
  expect(item).toHaveTextContent('headline: 84 (maximum 80)');
});

it('shows only the provider error when Ollama reports a failure', async () => {
  const error = 'Ollama disconnected';
  const container = await generate(502, { ...failedBatch, error });

  expect(screen.getByText(error)).toBeInTheDocument();
  expect(toast.error).toHaveBeenCalledWith(error);
  expect(container).not.toHaveTextContent('0 of 2 succeeded');
  expect(container).not.toHaveTextContent(/Item \d+ in this batch/i);
});

it('shows the counts and retry hint when there are no failure details', async () => {
  const container = await generate(502, failedBatch);

  expect(container).toHaveTextContent('0 of 2 succeeded. 2 failed. Click “Generate 2 more” to retry.');
  expect(container).not.toHaveTextContent(/Item \d+ in this batch/i);
});
