import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom';
import SandboxConfigPanel from '@/components/csm-settings/sandbox/SandboxConfigPanel';
import type { SandboxConfig } from '@/components/csm-settings/sandbox/useRoutingSandbox';
import type { ExperienceGroupListItem } from '@/types/experienceGroup';
import type { SupportChannelListItem } from '@/types/supportChannel';

const GROUPS = [
  { id: 1, name: 'VIP' },
  { id: 2, name: 'Standard' },
] as ExperienceGroupListItem[];

const channel = (id: number, name: string, groupIds: number[]) => ({
  id,
  display_name: name,
  channel_type: 'live_chat',
  is_active: true,
  experience_groups: groupIds.map((g) => ({ id: g, name: `Group ${g}` })),
}) as unknown as SupportChannelListItem;

const CHANNELS = [
  channel(10, 'VIP chat', [1]),
  channel(11, 'Shared chat', [1, 2]),
  channel(12, 'Standard chat', [2]),
];

function renderPanel(config: Partial<SandboxConfig>) {
  const onChange = jest.fn();
  render(
    <SandboxConfigPanel
      config={{
        experienceGroupId: 1,
        supportChannelId: null,
        customerOrganisationId: null,
        subject: '',
        simulatedAt: '',
        ...config,
      }}
      experienceGroups={GROUPS}
      channels={CHANNELS}
      organisations={[]}
      onChange={onChange}
      onReset={jest.fn()}
    />,
  );
  return onChange;
}

async function choose(label: RegExp, option: string) {
  fireEvent.click(screen.getByLabelText(label));
  await waitFor(() => expect(screen.getByRole('option', { name: option })).toBeInTheDocument());
  fireEvent.click(screen.getByRole('option', { name: option }));
}

describe('SandboxConfigPanel channels', () => {
  it("offers only the selected group's channels", async () => {
    renderPanel({ experienceGroupId: 1 });
    fireEvent.click(screen.getByLabelText(/channel/i));
    await waitFor(() => expect(screen.getByRole('option', { name: /VIP chat/ })).toBeInTheDocument());
    expect(screen.getByRole('option', { name: /Shared chat/ })).toBeInTheDocument();
    expect(screen.queryByRole('option', { name: /Standard chat/ })).not.toBeInTheDocument();
  });

  it('clears the channel when the new group does not include it', async () => {
    const onChange = renderPanel({ experienceGroupId: 1, supportChannelId: 10 });
    await choose(/experience group/i, 'Standard');
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({
      experienceGroupId: 2, supportChannelId: null,
    }));
  });

  it('keeps the channel when the new group includes it', async () => {
    const onChange = renderPanel({ experienceGroupId: 1, supportChannelId: 11 });
    await choose(/experience group/i, 'Standard');
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({
      experienceGroupId: 2, supportChannelId: 11,
    }));
  });

  it("does not offer another group's channel after switching groups", async () => {
    renderPanel({ experienceGroupId: 2 });
    fireEvent.click(screen.getByLabelText(/channel/i));
    await waitFor(() => expect(screen.getByRole('option', { name: /Standard chat/ })).toBeInTheDocument());
    expect(screen.queryByRole('option', { name: /VIP chat/ })).not.toBeInTheDocument();
  });
});
