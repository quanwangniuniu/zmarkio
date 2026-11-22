import { useState } from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { axe, toHaveNoViolations } from 'jest-axe';
import { Dialog, DialogContent, DialogDescription, DialogTitle } from '@/components/ui/dialog';

expect.extend(toHaveNoViolations);

function DialogHarness() {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button type="button" onClick={() => setOpen(true)}>
        Open
      </button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent>
          <DialogTitle>Invite member</DialogTitle>
          <DialogDescription>Send an invitation by email.</DialogDescription>
          <input aria-label="Email" />
        </DialogContent>
      </Dialog>
    </>
  );
}

describe('Dialog', () => {
  it('exposes a named, described modal dialog and passes axe', async () => {
    const user = userEvent.setup();
    const { baseElement } = render(<DialogHarness />);
    await user.click(screen.getByRole('button', { name: 'Open' }));

    const dialog = screen.getByRole('dialog', { name: 'Invite member' });
    expect(dialog).toHaveAccessibleDescription('Send an invitation by email.');
    expect(await axe(baseElement)).toHaveNoViolations();
  });

  it('restores focus to the opener when it is not a DialogTrigger', async () => {
    const user = userEvent.setup();
    render(<DialogHarness />);
    const opener = screen.getByRole('button', { name: 'Open' });
    await user.click(opener);
    expect(screen.getByRole('textbox', { name: 'Email' })).toHaveFocus();

    await user.keyboard('{Escape}');

    await waitFor(() => expect(opener).toHaveFocus());
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });
});
