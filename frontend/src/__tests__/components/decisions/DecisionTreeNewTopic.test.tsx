import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import '@testing-library/jest-dom';
import DecisionTree from '@/components/decisions/DecisionTree';
import type { DecisionGraphNode, DecisionGraphTopic } from '@/types/decision';

jest.mock('@/lib/buildUrl', () => ({ useBuildUrl: () => (path: string) => path }));

beforeAll(() => {
  global.ResizeObserver ??= class {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as unknown as typeof ResizeObserver;
  Element.prototype.scrollIntoView ??= () => {};
});

// The decision map of an Australian paid media team, grouped by topic.
const TOPICS: DecisionGraphTopic[] = [
  { topic: 'eofy-sale', title: 'EOFY Sale' },
  { topic: 'click-frenzy', title: 'Click Frenzy' },
];

const node = (id: number, title: string, topic: string): DecisionGraphNode => ({
  id,
  title,
  topic,
  topicLabel: TOPICS.find((t) => t.topic === topic)?.title,
  status: 'COMMITTED',
  projectId: 12,
  projectSeq: id,
  createdAt: '2026-06-02T00:00:00Z',
  updatedAt: '2026-06-02T00:00:00Z',
});

const NODES = [
  node(41, 'Cap EOFY Meta spend at $40k', 'eofy-sale'),
  node(42, 'Run Click Frenzy on Google Shopping only', 'click-frenzy'),
];

function renderMap(onCreateTopic = jest.fn().mockResolvedValue(undefined)) {
  const user = userEvent.setup();
  render(
    <DecisionTree
      nodes={NODES}
      edges={[]}
      topics={TOPICS}
      projectId={12}
      viewMode="topics"
      onCreateTopic={onCreateTopic}
    />,
  );
  return { user, onCreateTopic };
}

async function startNewTopic(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole('button', { name: /Add Topic/ }));
  return screen.getByLabelText('New topic name') as HTMLInputElement;
}

describe('Decision map — adding a topic', () => {
  it('creates "Boxing Day Sales" without showing any key', async () => {
    const { user, onCreateTopic } = renderMap();

    const input = await startNewTopic(user);
    await user.type(input, 'Boxing Day Sales');
    expect(screen.queryByText(/Saved as/)).not.toBeInTheDocument();
    expect(screen.queryByText(/boxing-day-sales/)).not.toBeInTheDocument();
    await user.keyboard('{Enter}');

    expect(onCreateTopic).toHaveBeenCalledWith('Boxing Day Sales');
    await waitFor(() => expect(screen.queryByLabelText('New topic name')).not.toBeInTheDocument());
  });

  it('stops a planner from adding a topic that is already on the map', async () => {
    const { user, onCreateTopic } = renderMap();

    const input = await startNewTopic(user);
    await user.type(input, 'eofy sale');

    expect(screen.getByText('A topic named "eofy sale" already exists.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Save topic' })).toBeDisabled();
    await user.keyboard('{Enter}');
    expect(onCreateTopic).not.toHaveBeenCalled();
  });

  it('lets the planner fix a duplicate name and save', async () => {
    const { user, onCreateTopic } = renderMap();

    const input = await startNewTopic(user);
    await user.type(input, 'Click Frenzy');
    expect(screen.getByText('A topic named "Click Frenzy" already exists.')).toBeInTheDocument();
    await user.type(input, ' Mid Year');

    expect(screen.queryByText(/already exists/)).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Save topic' }));
    expect(onCreateTopic).toHaveBeenCalledWith('Click Frenzy Mid Year');
  });

  it('accepts a name with no letters, such as an emoji', async () => {
    const { user, onCreateTopic } = renderMap();

    const input = await startNewTopic(user);
    await user.type(input, '🎄');
    await user.keyboard('{Enter}');

    expect(onCreateTopic).toHaveBeenCalledWith('🎄');
  });

  it('does not accept an empty name', async () => {
    const { user, onCreateTopic } = renderMap();

    await startNewTopic(user);
    await user.keyboard('   {Enter}');

    expect(screen.getByText('Enter a topic name.')).toBeInTheDocument();
    expect(onCreateTopic).not.toHaveBeenCalled();
  });

  it('keeps the name and shows the server error when saving fails', async () => {
    const onCreateTopic = jest.fn().mockRejectedValue(new Error('Title must be 80 characters or fewer.'));
    const { user } = renderMap(onCreateTopic);

    const input = await startNewTopic(user);
    await user.type(input, 'Boxing Day Sales');
    await user.keyboard('{Enter}');

    expect(await screen.findByText('Title must be 80 characters or fewer.')).toBeInTheDocument();
    expect(input).toHaveValue('Boxing Day Sales');
  });

  it('saves only once when the planner double-presses Enter', async () => {
    let finish: () => void = () => {};
    const onCreateTopic = jest.fn(() => new Promise<void>((resolve) => { finish = resolve; }));
    const { user } = renderMap(onCreateTopic);

    const input = await startNewTopic(user);
    await user.type(input, 'Boxing Day Sales');
    await user.keyboard('{Enter}{Enter}');
    await act(async () => finish());

    expect(onCreateTopic).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(screen.queryByLabelText('New topic name')).not.toBeInTheDocument());
  });

  it('stops the name at 80 characters as the planner types', async () => {
    const { user } = renderMap();
    const name = 'Christmas and Boxing Day paid social push for AU and NZ, SEA to follow (draft-1)';

    const input = await startNewTopic(user);
    await user.type(input, name.slice(0, 79));
    expect(screen.queryByText('Max 80 characters.')).not.toBeInTheDocument();

    await user.type(input, `${name.slice(79)} — final`);
    expect(input.value).toBe(name);
    expect(screen.getByText('Max 80 characters.')).toBeInTheDocument();

    await user.keyboard('{Backspace}');
    expect(screen.queryByText('Max 80 characters.')).not.toBeInTheDocument();
  });

  it('tells the planner when a pasted name was cut, and stops saying so once they shorten it', async () => {
    const { user, onCreateTopic } = renderMap();
    const pasted = 'Christmas and Boxing Day paid social push for AU and NZ, SEA to follow (draft-1) — owner: paid social lead';

    const input = await startNewTopic(user);
    await user.paste(pasted);

    expect(input.value).toBe(pasted.slice(0, 80));
    expect(screen.getByText('Pasted text was cut to fit 80 characters.')).toBeInTheDocument();

    await user.keyboard('{Backspace}');
    expect(screen.queryByText(/Pasted text was cut/)).not.toBeInTheDocument();

    await user.keyboard('{Enter}');
    expect(onCreateTopic).toHaveBeenCalledWith(pasted.slice(0, 79).trim());
  });

  it('does not warn when a pasted name fits', async () => {
    const { user } = renderMap();

    const input = await startNewTopic(user);
    await user.paste('Boxing Day Sales');

    expect(input).toHaveValue('Boxing Day Sales');
    expect(screen.queryByText(/Pasted text was cut|Max 80 characters/)).not.toBeInTheDocument();
  });

  it('shows the duplicate-name error rather than the limit notice when both apply', async () => {
    const onCreateTopic = jest.fn();
    const longExisting = 'Christmas and Boxing Day paid social push for AU and NZ, SEA to follow (draft-1)';
    const user = userEvent.setup();
    render(
      <DecisionTree
        nodes={NODES}
        edges={[]}
        topics={[...TOPICS, { topic: 'christmas-push', title: longExisting }]}
        projectId={12}
        viewMode="topics"
        onCreateTopic={onCreateTopic}
      />,
    );

    const input = await startNewTopic(user);
    await user.type(input, longExisting);

    expect(screen.getByText(`A topic named "${longExisting}" already exists.`)).toBeInTheDocument();
    expect(screen.queryByText('Max 80 characters.')).not.toBeInTheDocument();
  });
});
