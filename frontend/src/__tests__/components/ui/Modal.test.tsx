import { useState } from 'react';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { axe, toHaveNoViolations } from 'jest-axe';
import Modal from '@/components/ui/Modal';

expect.extend(toHaveNoViolations);

function ModalHarness({ ariaLabel }: { ariaLabel?: string }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button type="button" onClick={() => setOpen(true)}>
        Open
      </button>
      <Modal isOpen={open} onClose={() => setOpen(false)} ariaLabel={ariaLabel}>
        <div>
          <h2>Edit region</h2>
          <input aria-label="Name" />
          <button type="button" onClick={() => setOpen(false)}>
            Save
          </button>
        </div>
      </Modal>
    </>
  );
}

describe('Modal', () => {
  it('exposes a modal dialog named by its heading', async () => {
    const user = userEvent.setup();
    const { baseElement } = render(<ModalHarness />);
    await user.click(screen.getByRole('button', { name: 'Open' }));

    const dialog = screen.getByRole('dialog', { name: 'Edit region' });
    expect(dialog).toHaveAttribute('aria-modal', 'true');
    expect(await axe(baseElement)).toHaveNoViolations();
  });

  it('uses ariaLabel as the name when given', async () => {
    const user = userEvent.setup();
    render(<ModalHarness ariaLabel="Region editor" />);
    await user.click(screen.getByRole('button', { name: 'Open' }));

    expect(screen.getByRole('dialog', { name: 'Region editor' })).not.toHaveAttribute('aria-labelledby');
  });

  it('moves focus in, keeps Tab inside and restores focus on close', async () => {
    const user = userEvent.setup();
    render(<ModalHarness />);
    const trigger = screen.getByRole('button', { name: 'Open' });
    await user.click(trigger);

    const dialog = screen.getByRole('dialog');
    const name = screen.getByRole('textbox', { name: 'Name' });
    const save = screen.getByRole('button', { name: 'Save' });
    expect(dialog).toHaveFocus();

    await user.tab();
    expect(name).toHaveFocus();
    await user.tab();
    expect(save).toHaveFocus();
    await user.tab();
    expect(name).toHaveFocus();
    await user.tab({ shift: true });
    expect(save).toHaveFocus();

    await user.keyboard('{Escape}');
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });
});
