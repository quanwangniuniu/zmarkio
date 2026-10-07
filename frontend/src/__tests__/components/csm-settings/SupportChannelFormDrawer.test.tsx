import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom';
import SupportChannelFormDrawer from '@/components/csm-settings/SupportChannelFormDrawer';

async function selectPortalOption(labelPattern: RegExp, optionName: string) {
  fireEvent.click(screen.getByLabelText(labelPattern));
  await waitFor(() => {
    expect(screen.getByRole('option', { name: optionName })).toBeInTheDocument();
  });
  fireEvent.click(screen.getByRole('option', { name: optionName }));
}

jest.mock('@/lib/api/supportChannelApi', () => ({
  SupportChannelAPI: {
    list: jest.fn().mockResolvedValue([]),
    getEmbedSnippet: jest.fn(),
    create: jest.fn(),
    update: jest.fn(),
    replaceExperienceGroups: jest.fn(),
    retrieve: jest.fn(),
  },
}));

jest.mock('@/lib/api/csmApi', () => ({
  __esModule: true,
  default: {
    listProjectQueues: jest.fn().mockResolvedValue([{ id: 1, name: 'Frontline' }]),
  },
}));

jest.mock('@/lib/api/ticketFormApi', () => ({
  TicketFormAPI: {
    list: jest.fn().mockResolvedValue([{ id: 7, name: 'Default Form' }]),
  },
}));

jest.mock('@/lib/api/experienceGroupApi', () => ({
  ExperienceGroupAPI: {
    list: jest.fn().mockResolvedValue({ data: [{ id: 10, name: 'VIP Support' }] }),
  },
}));

jest.mock('react-hot-toast', () => ({
  __esModule: true,
  default: { success: jest.fn(), error: jest.fn() },
}));

describe('SupportChannelFormDrawer', () => {
  const onClose = jest.fn();
  const onSaved = jest.fn();

  beforeEach(() => {
    jest.clearAllMocks();
  });

  it('renders drawer title and general fields when open', async () => {
    render(
      <SupportChannelFormDrawer
        isOpen
        projectId={1}
        editing={null}
        onClose={onClose}
        onSaved={onSaved}
      />,
    );
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(screen.getByText('New support channel')).toBeInTheDocument();
    expect(screen.getByLabelText(/display name/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/channel type/i)).toBeInTheDocument();
    expect(await screen.findByLabelText(/default queue/i)).toBeInTheDocument();
  });

  it('does not render when closed', () => {
    render(
      <SupportChannelFormDrawer
        isOpen={false}
        projectId={1}
        editing={null}
        onClose={onClose}
        onSaved={onSaved}
      />,
    );
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('shows queue dropdown for live chat', async () => {
    render(
      <SupportChannelFormDrawer
        isOpen
        projectId={1}
        editing={null}
        onClose={onClose}
        onSaved={onSaved}
      />,
    );
    expect(await screen.findByLabelText(/default queue/i)).toBeInTheDocument();
    // Let the drawer finish its initial focus before opening the popover.
    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Close' })).toHaveFocus();
    });
    fireEvent.click(screen.getByLabelText(/default queue/i));
    expect(
      await screen.findByRole('option', { name: 'Frontline' }, { timeout: 3000 }),
    ).toBeInTheDocument();
  });

  it('shows ticket form dropdown when type is contact_form', async () => {
    render(
      <SupportChannelFormDrawer
        isOpen
        projectId={1}
        editing={null}
        onClose={onClose}
        onSaved={onSaved}
      />,
    );
    await selectPortalOption(/channel type/i, 'Contact form');
    expect(await screen.findByLabelText(/ticket form/i)).toBeInTheDocument();
  });

  it('shows email input when type is email', async () => {
    render(
      <SupportChannelFormDrawer
        isOpen
        projectId={1}
        editing={null}
        onClose={onClose}
        onSaved={onSaved}
      />,
    );
    await selectPortalOption(/channel type/i, 'Email');
    expect(screen.getByLabelText(/email address/i)).toBeInTheDocument();
  });

  it('shows experience groups multi-select when groups exist', async () => {
    render(
      <SupportChannelFormDrawer
        isOpen
        projectId={1}
        editing={null}
        onClose={onClose}
        onSaved={onSaved}
      />,
    );
    expect(await screen.findByRole('combobox')).toBeInTheDocument();
    expect(screen.getByText(/select experience groups/i)).toBeInTheDocument();
    fireEvent.click(screen.getByText(/select experience groups/i));
    expect(await screen.findByRole('option', { name: 'VIP Support' })).toBeInTheDocument();
  });

  it('calls onClose when close button is clicked', () => {
    render(
      <SupportChannelFormDrawer
        isOpen
        projectId={1}
        editing={null}
        onClose={onClose}
        onSaved={onSaved}
      />,
    );
    fireEvent.click(screen.getByLabelText('Close'));
    expect(onClose).toHaveBeenCalled();
  });
});
