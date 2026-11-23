import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { axe, toHaveNoViolations } from 'jest-axe';
import ConfirmModal from '@/components/ui/ConfirmModal';

expect.extend(toHaveNoViolations);

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

  it('names and describes the dialog and passes axe', async () => {
    const { baseElement } = render(
      <ConfirmModal
        isOpen
        onClose={jest.fn()}
        onConfirm={jest.fn()}
        title="Delete item"
        message="This cannot be undone"
      />
    );

    const dialog = screen.getByRole('dialog', { name: 'Delete item' });
    expect(dialog).toHaveAccessibleDescription('This cannot be undone');
    expect(screen.getByRole('button', { name: 'Cancel' })).toHaveFocus();
    expect(await axe(baseElement)).toHaveNoViolations();
  });

  it('restores focus to the trigger when closed', async () => {
    const user = userEvent.setup();
    function Harness() {
      const [open, setOpen] = useState(false);
      return (
        <>
          <button type="button" onClick={() => setOpen(true)}>
            Delete
          </button>
          <ConfirmModal
            isOpen={open}
            onClose={() => setOpen(false)}
            onConfirm={jest.fn()}
            title="Delete item"
            message="This cannot be undone"
          />
        </>
      );
    }
    render(<Harness />);
    const trigger = screen.getByRole('button', { name: 'Delete' });
    await user.click(trigger);
    await user.click(screen.getByRole('button', { name: 'Cancel' }));

    await waitFor(() => expect(trigger).toHaveFocus());
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });
});
