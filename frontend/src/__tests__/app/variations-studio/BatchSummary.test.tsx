import React from 'react';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
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
jest.mock('react-hot-toast', () => ({
  __esModule: true,
  default: Object.assign(jest.fn(), { error: jest.fn(), success: jest.fn() }),
}));

const platformsUrl = '/api/ad_copy_variation/platforms/';
const draftsUrl = '/api/ad_copy_variation/variations/';
const platforms = [
  {
    id: 'meta', display_name: 'Meta',
    fields: ['Hook', 'Headline', 'Description', 'CTA'].map(label => ({
      key: label.toLowerCase(), label, required: true, type: 'string', limits: {},
    })),
    cta: { kind: 'enum', field: 'cta', values: ['SHOP_NOW'] },
  },
  {
    id: 'text_only', display_name: 'Text only',
    fields: [
      { key: 'main_line', label: 'Main line', type: 'string', required: true, limits: {} },
      { key: 'tagline', label: 'Tagline', type: 'string', required: false, limits: {} },
    ],
    cta: { kind: 'none' },
  },
];
const metaCopy = { hook: 'Saved hook', headline: 'Saved headline', description: 'Saved description', cta: 'SHOP_NOW' };
const metaDraft = {
  id: 7, slug: 'saved-headline', platform: 'meta', status: 'draft', source_mode: 'custom',
  batch_id: 'batch-1', ...metaCopy, fields: metaCopy,
};
const textDraft = {
  id: 8, slug: 'saved-main-line', platform: 'text_only', status: 'draft', source_mode: 'custom',
  batch_id: 'batch-2', hook: '', headline: '', description: '', cta: '',
  fields: { main_line: 'Saved main line', tagline: 'Saved tagline' },
};

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
  const button = screen.getByRole('button', { name: 'Generate 2 variations' });
  await waitFor(() => expect(button).toBeEnabled());
  fireEvent.click(button);
  await screen.findByRole('button', { name: 'Generate 2 variations' });
  return container;
}

beforeEach(() => {
  jest.spyOn(api, 'get').mockImplementation(async url => {
    if (url === platformsUrl) return { data: { platforms } };
    throw new Error(`Unexpected GET ${url}`);
  });
});

afterEach(() => {
  jest.restoreAllMocks();
  jest.clearAllMocks();
});

it('shows the original failed item number beside a saved card on partial success', async () => {
  const container = await generate(200, {
    ...failedBatch, count_succeeded: 1, count_failed: 1, failed_indices: [1],
    results: [metaDraft],
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

it('disables generation and explains why drafts are unavailable when platform discovery fails', async () => {
  jest.mocked(api.get).mockRejectedValueOnce(new Error('Platform discovery unavailable'));
  const post = jest.spyOn(api, 'post');
  render(<VariationsStudioPage />);
  const generate = screen.getByRole('button', { name: 'Generate 10 variations' });

  expect(screen.getByText('Loading platforms…')).toBeVisible();
  expect(generate).toBeDisabled();
  await waitFor(() => expect(toast.error).toHaveBeenCalled());
  expect(screen.queryByText('Loading platforms…')).not.toBeInTheDocument();
  expect(generate).toBeDisabled();
  fireEvent.click(generate);
  expect(post).not.toHaveBeenCalled();

  fireEvent.click(screen.getByRole('button', { name: 'AI Drafts' }));
  expect(screen.getByText('Platform information is unavailable. AI drafts cannot be displayed.')).toBeVisible();
  expect(screen.queryByText('No AI drafts match these filters.')).not.toBeInTheDocument();
});

it('uses the selected platform for generation and each saved card’s own fields for display and editing', async () => {
  const drafts = [textDraft, metaDraft];
  const post = jest.spyOn(api, 'post').mockImplementation(async url => {
    if (url === '/api/ad_copy_variation/initialize/') return { data: {} };
    const draft = drafts.shift();
    if (url !== `${draftsUrl}generate/` || !draft) throw new Error(`Unexpected POST ${url}`);
    return { data: {
      batch_id: draft.batch_id, count_requested: 1, count_succeeded: 1,
      count_failed: 0, results: [draft], failed_indices: [],
    } };
  });
  const edited = { ...textDraft.fields, main_line: 'Edited on the card' };
  const patch = jest.spyOn(api, 'patch').mockResolvedValue({ data: { ...textDraft, fields: edited } });
  render(<VariationsStudioPage />);
  const custom = within(screen.getByRole('tabpanel', { name: 'Custom content' }));
  const platform = screen.getByRole('combobox', { name: 'Platform' });
  await custom.findByRole('textbox', { name: 'Headline' });
  expect(platform).toHaveValue('meta');
  fireEvent.change(screen.getByLabelText('Custom count'), { target: { value: '1' } });
  fireEvent.change(platform, { target: { value: 'text_only' } });
  const mainLine = custom.getByRole('textbox', { name: 'Main line' });
  const tagline = custom.getByRole('textbox', { name: 'Tagline' });
  expect(custom.getAllByRole('textbox')).toEqual([mainLine, tagline]);
  fireEvent.change(mainLine, { target: { value: 'Original line' } });
  fireEvent.change(tagline, { target: { value: 'Original tagline' } });
  fireEvent.click(screen.getByRole('button', { name: 'Generate 1 variation' }));
  const first = within(await screen.findByRole('article'));
  expect(post).toHaveBeenCalledWith(`${draftsUrl}generate/`, expect.objectContaining({
    platform: 'text_only', base_copy: { main_line: 'Original line', tagline: 'Original tagline' },
  }), expect.anything());

  fireEvent.change(platform, { target: { value: 'meta' } });
  expect(first.getByText('Saved main line')).toBeVisible();
  expect(first.queryByText(/^CTA:?$/i)).not.toBeInTheDocument();
  const cta = custom.getByRole('textbox', { name: 'CTA' });
  fireEvent.change(cta, { target: { value: 'Visit the shop' } });
  fireEvent.click(screen.getByRole('button', { name: 'Generate 1 more' }));
  await screen.findByText('Saved headline');
  expect(post).toHaveBeenLastCalledWith(`${draftsUrl}generate/`, expect.objectContaining({
    platform: 'meta', base_copy: { cta: 'Visit the shop' },
  }), expect.anything());
  const [firstCard, secondCard] = screen.getAllByRole('article');
  expect(firstCard).toHaveTextContent(/Main line.*Saved main line.*Tagline.*Saved tagline/);
  expect(secondCard).toHaveTextContent('SHOP_NOW');
  fireEvent.click(first.getByRole('button', { name: 'Edit variation 1' }));
  fireEvent.change(first.getByRole('textbox', { name: 'Main line' }), { target: { value: edited.main_line } });
  fireEvent.click(first.getByRole('button', { name: 'Update' }));
  await first.findByText(edited.main_line);
  expect(patch).toHaveBeenCalledWith(`${draftsUrl}${textDraft.slug}/`, { fields: edited });
});

it('summarizes, cancels edits and updates an AI Draft using the draft’s platform and slug', async () => {
  const row = { ...textDraft, source_mode: 'existing', creative: 42, created_at: '2026-01-01T00:00:00Z' };
  const edited = { ...row.fields, tagline: 'Edited in AI Drafts' };
  jest.spyOn(api, 'post').mockResolvedValue({ data: {} });
  const patch = jest.spyOn(api, 'patch').mockResolvedValue({ data: { ...row, fields: edited } });
  render(<VariationsStudioPage />);
  await screen.findByRole('textbox', { name: 'Headline' });
  fireEvent.change(screen.getByRole('combobox', { name: 'Platform' }), { target: { value: row.platform } });
  jest.mocked(api.get).mockResolvedValueOnce({ data: { results: [row], count: 1 } });
  fireEvent.click(screen.getByRole('button', { name: 'AI Drafts' }));
  const article = await screen.findByRole('article');
  expect(screen.getByRole('combobox', { name: 'Platform' })).toHaveValue(row.platform);
  expect(api.get).toHaveBeenLastCalledWith(draftsUrl, { params: expect.objectContaining({ platform: row.platform }) });
  const summary = within(article);
  expect(article).toHaveTextContent('Platform: Text only');
  expect(article).toHaveTextContent(/Main line.*Saved main line.*Tagline.*Saved tagline/);
  expect(summary.queryByText(/^CTA:?$/i)).not.toBeInTheDocument();
  expect(summary.getByRole('link', { name: 'Creative detail' })).toHaveAttribute('href', '/meta-ads/creatives/42');

  fireEvent.click(summary.getByRole('button', { name: 'Edit draft 8' }));
  fireEvent.change(summary.getByRole('textbox', { name: 'Tagline' }), { target: { value: 'Unsaved edit' } });
  fireEvent.click(summary.getByRole('button', { name: 'Cancel' }));
  expect(article).toHaveTextContent('Saved tagline');
  expect(patch).not.toHaveBeenCalled();

  fireEvent.click(summary.getByRole('button', { name: 'Edit draft 8' }));
  const tagline = summary.getByRole('textbox', { name: 'Tagline' });
  expect(tagline).toHaveValue('Saved tagline');
  fireEvent.change(tagline, { target: { value: edited.tagline } });
  fireEvent.click(summary.getByRole('button', { name: 'Update' }));
  await waitFor(() => expect(article).toHaveTextContent(edited.tagline));
  expect(patch).toHaveBeenCalledWith(`${draftsUrl}${row.slug}/`, { fields: edited });
});

it('generates with the first registered platform and opens reviewed drafts on their own platform', async () => {
  const reviewed = { ...textDraft, status: 'reviewed' };
  const get = jest.mocked(api.get);
  get.mockResolvedValueOnce({ data: { platforms: [platforms[1], { ...platforms[0], id: 'other', display_name: 'Other' }] } });
  const post = jest.spyOn(api, 'post').mockImplementation(async url => {
    if (url === '/api/ad_copy_variation/initialize/') return { data: {} };
    if (url === `${draftsUrl}generate/`) return { data: {
      batch_id: textDraft.batch_id, count_requested: 1, count_succeeded: 1,
      count_failed: 0, results: [textDraft], failed_indices: [],
    } };
    if (url === `${draftsUrl}review_batch/`) return { data: { reviewed_count: 1, results: [reviewed] } };
    throw new Error(`Unexpected POST ${url}`);
  });

  render(<VariationsStudioPage />);
  await screen.findByRole('textbox', { name: 'Main line' });
  const target = screen.getByRole('combobox', { name: 'Platform' });
  expect(target).toHaveValue(textDraft.platform);
  fireEvent.change(screen.getByLabelText('Custom count'), { target: { value: '1' } });
  fireEvent.click(screen.getByRole('button', { name: 'Generate 1 variation' }));
  const card = within(await screen.findByRole('article'));
  expect(post).toHaveBeenCalledWith(`${draftsUrl}generate/`, expect.objectContaining({ platform: textDraft.platform }), expect.anything());

  fireEvent.change(target, { target: { value: 'other' } });
  fireEvent.click(card.getByRole('checkbox', { name: 'Select variation 1 for review' }));
  get.mockResolvedValueOnce({ data: { results: [reviewed], count: 1 } });
  fireEvent.click(screen.getByRole('button', { name: 'Review selected (1)' }));
  await screen.findByRole('button', { name: `Edit draft ${reviewed.id}` });
  expect(get.mock.calls.filter(([url]) => url === draftsUrl)).toEqual([
    [draftsUrl, { params: expect.objectContaining({ project_id: 1, platform: reviewed.platform, page: 1 }) }],
  ]);
  expect(screen.getByRole('combobox', { name: 'Platform' })).toHaveValue(reviewed.platform);
  fireEvent.click(screen.getByRole('button', { name: 'Generate' }));
  expect(screen.getByRole('combobox', { name: 'Platform' })).toHaveValue('other');
});

it('follows the latest batch across platforms and resets the batch and page on a manual platform change', async () => {
  const get = jest.mocked(api.get);
  jest.spyOn(api, 'post').mockResolvedValue({ data: {} });
  render(<VariationsStudioPage />);
  await screen.findByRole('textbox', { name: 'Headline' });
  get.mockResolvedValueOnce({ data: { results: [metaDraft], count: 11 } });
  fireEvent.click(screen.getByRole('button', { name: 'AI Drafts' }));
  expect(await screen.findByRole('article')).toHaveTextContent('Platform: Meta');
  const platform = screen.getByRole('combobox', { name: 'Platform' });
  const latest = screen.getByRole('button', { name: 'Latest batch' });
  const next = screen.getByRole('button', { name: 'Next' });
  expect(platform).toHaveValue(metaDraft.platform);

  const secondMetaDraft = { ...metaDraft, id: 17, fields: { ...metaCopy, headline: 'Next Meta draft' } };
  get.mockResolvedValueOnce({ data: { results: [secondMetaDraft], count: 11 } });
  fireEvent.click(next);
  await screen.findByText('Next Meta draft');
  get.mockResolvedValueOnce({ data: { batch_id: textDraft.batch_id, results: [textDraft] } })
    .mockResolvedValueOnce({ data: { results: [textDraft], count: 11 } });
  fireEvent.click(latest);
  expect(await screen.findByText('Saved main line')).toBeVisible();
  expect(get).toHaveBeenCalledWith(`${draftsUrl}latest_batch/`, { params: { project_id: 1 } });
  expect(platform).toHaveValue(textDraft.platform);
  expect(screen.getByText(textDraft.batch_id)).toBeVisible();

  const secondTextDraft = { ...textDraft, id: 18, fields: { ...textDraft.fields, main_line: 'Next text draft' } };
  get.mockResolvedValueOnce({ data: { results: [secondTextDraft], count: 11 } });
  fireEvent.click(next);
  await screen.findByText('Next text draft');
  get.mockResolvedValueOnce({ data: { batch_id: null, results: [] } });
  fireEvent.click(latest);
  await waitFor(() => expect(toast).toHaveBeenCalledWith('No generated batches yet.'));
  expect(platform).toHaveValue(textDraft.platform);
  expect(screen.getByText(textDraft.batch_id)).toBeVisible();
  expect(screen.getByText(/page 2 of 2/)).toBeVisible();

  get.mockResolvedValueOnce({ data: { results: [metaDraft], count: 11 } });
  fireEvent.change(platform, { target: { value: metaDraft.platform } });
  expect(await screen.findByText('Saved headline')).toBeVisible();
  expect(screen.queryByText(textDraft.batch_id)).not.toBeInTheDocument();
  const firstPage = { project_id: 1, page_size: 10, page: 1, platform: metaDraft.platform };
  const batchPage = { ...firstPage, platform: textDraft.platform, batch_id: textDraft.batch_id };
  expect(get.mock.calls.filter(([url]) => url === draftsUrl).map(([, config]) => config?.params)).toEqual([
    firstPage, { ...firstPage, page: 2 }, batchPage, { ...batchPage, page: 2 }, firstPage,
  ]);
});
