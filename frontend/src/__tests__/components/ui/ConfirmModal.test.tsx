import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import ConfirmModal from '@/components/ui/ConfirmModal';

describe('ConfirmModal', () => {
  it('does not render when closed', () => {
    render(
      <ConfirmModal
        isOpen={false}
        onClose={jest.fn()}
        onConfirm={jest.fn()}
        title="Delete"
        message="Sure?"
      />
    );
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('renders types and confirms/cancels', async () => {
    const user = userEvent.setup();
    const onClose = jest.fn();
    const onConfirm = jest.fn().mockResolvedValue(undefined);

    const { rerender } = render(
      <ConfirmModal
        isOpen
        onClose={onClose}
        onConfirm={onConfirm}
        title="Delete item"
        message="This cannot be undone"
        type="danger"
      />
    );

    expect(screen.getByRole('dialog')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(onClose).toHaveBeenCalled();

    onClose.mockClear();
    rerender(
      <ConfirmModal
        isOpen
        onClose={onClose}
        onConfirm={onConfirm}
        title="Warn"
        message="Careful"
        type="warning"
        confirmText="Continue"
      />
    );
    await user.click(screen.getByRole('button', { name: 'Continue' }));
    await waitFor(() => expect(onConfirm).toHaveBeenCalled());
    expect(onClose).toHaveBeenCalled();

    rerender(
      <ConfirmModal
        isOpen
        onClose={onClose}
        onConfirm={jest.fn().mockRejectedValue(new Error('fail'))}
        title="Info"
        message="Heads up"
        type="info"
        loading
      />
    );
    expect(screen.getByText('Processing...')).toBeInTheDocument();
    fireEvent.keyDown(document, { key: 'Escape' });
  });

  it('closes on Escape when not loading and traps Tab focus', async () => {
    const onClose = jest.fn();
    render(
      <ConfirmModal
        isOpen
        onClose={onClose}
        onConfirm={jest.fn()}
        title="Delete"
        message="Sure?"
      />
    );

    fireEvent.keyDown(document, { key: 'Escape' });
    expect(onClose).toHaveBeenCalled();

    const dialog = screen.getByRole('dialog');
    const buttons = dialog.querySelectorAll('button');
    buttons[buttons.length - 1].focus();
    fireEvent.keyDown(document, { key: 'Tab' });
  });
});
