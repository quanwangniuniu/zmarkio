import { useState } from 'react';
import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import '@testing-library/jest-dom';
import DecisionOptionsSection from '@/components/decisions/detail/DecisionOptionsSection';
import type { DecisionOptionDraft } from '@/types/decision';

const codePoints = (text: string) => Array.from(text).length;

// What the planner already wrote for "Who produces the Q4 holiday creative?"
const SAVED_OPTIONS: DecisionOptionDraft[] = [
  { id: 1, decisionId: 48, text: 'Use the existing vendor for all Q4 creative', isSelected: true, order: 0 },
  { id: 2, decisionId: 48, text: 'Split video work with a new agency, vendor keeps static and display', isSelected: false, order: 1 },
];

// A vendor write-up pasted from the planning doc (about 1,200 characters).
const VENDOR_WRITE_UP =
  'Use the existing vendor for all Q4 creative production. They already know our brand guidelines and have delivered the last three campaigns on time, so onboarding cost is close to zero. ' +
  'Their current rate card is about ten percent higher than the two new agencies we spoke to, but both of those agencies would need at least three weeks to ramp up, which would push the first holiday assets past the November deadline. ' +
  'The vendor has confirmed they can add a second designer from mid-October to handle the extra volume for Black Friday and Cyber Monday. ' +
  'Risks: they have struggled with short-form video in the past, and two of the June TikTok edits needed a second round of revisions. ' +
  'To reduce this, we will ask for a test batch of five video variations in the first week and review them with the paid social team before approving the full brief. ' +
  'Budget impact is roughly within the approved creative line, with a small buffer for rush fees. ' +
  'If the test batch is not good enough, we fall back to splitting video work with one of the new agencies while the vendor keeps static and display. ' +
  'Final approval needed from the marketing lead by Friday so production can start the following Monday.';

/**
 * Plays the page's part: saves go to `api`, and the server's reply (with ids) is fed back in,
 * the same way the detail page re-renders after a PATCH.
 */
function renderOptions(api: jest.Mock = jest.fn().mockResolvedValue(undefined), initial = SAVED_OPTIONS) {
  function Page() {
    const [value, setValue] = useState(initial);
    return (
      <DecisionOptionsSection
        value={value}
        editable
        onSave={async (next) => {
          await api(next);
          setValue(next.map((o, i) => ({ ...o, id: 100 + i, decisionId: 48 })));
        }}
      />
    );
  }
  const user = userEvent.setup();
  render(<Page />);
  return { user, api };
}

const field = (n: number) => screen.getByRole('textbox', { name: `Option ${n}` }) as HTMLTextAreaElement;
const sent = (api: jest.Mock, call = 0) =>
  (api.mock.calls[call][0] as DecisionOptionDraft[]).map(({ text, isSelected, order }) => ({ text, isSelected, order }));

describe('Decision options — what a planner does with them', () => {
  it('adds an option and starts typing in it, without saving a blank option first', async () => {
    const { user, api } = renderOptions();

    await user.click(screen.getByRole('button', { name: 'Add option' }));

    expect(field(3)).toHaveFocus();
    expect(api).not.toHaveBeenCalled();
  });

  it('asks for text when the planner leaves a new option empty, and sends nothing', async () => {
    const { user, api } = renderOptions();

    await user.click(screen.getByRole('button', { name: 'Add option' }));
    await user.click(document.body);

    expect(field(3)).toHaveAttribute('aria-invalid', 'true');
    expect(screen.getByText('Enter text, or remove this option.')).toBeInTheDocument();
    expect(api).not.toHaveBeenCalled();
  });

  it('treats an option of only spaces as empty', async () => {
    const { user, api } = renderOptions();

    await user.click(screen.getByRole('button', { name: 'Add option' }));
    await user.keyboard('   ');
    await user.click(document.body);

    expect(screen.getByText('Enter text, or remove this option.')).toBeInTheDocument();
    expect(api).not.toHaveBeenCalled();
  });

  it('saves the new option once it has text, keeping the existing choice', async () => {
    const { user, api } = renderOptions();

    await user.click(screen.getByRole('button', { name: 'Add option' }));
    await user.click(document.body);
    await user.click(field(3));
    await user.keyboard('Bring all creative in-house with a contract designer');
    expect(screen.queryByText('Enter text, or remove this option.')).not.toBeInTheDocument();
    await user.click(document.body);

    expect(api).toHaveBeenCalledTimes(1);
    expect(sent(api)).toEqual([
      { text: 'Use the existing vendor for all Q4 creative', isSelected: true, order: 0 },
      { text: 'Split video work with a new agency, vendor keeps static and display', isSelected: false, order: 1 },
      { text: 'Bring all creative in-house with a contract designer', isSelected: false, order: 2 },
    ]);
  });

  it('removes an option that was added by mistake without a flash of error or a request', async () => {
    const { user, api } = renderOptions();

    await user.click(screen.getByRole('button', { name: 'Add option' }));
    await user.click(screen.getByRole('button', { name: 'Delete option 3' }));

    expect(screen.queryByRole('textbox', { name: 'Option 3' })).not.toBeInTheDocument();
    expect(screen.queryByText('Enter text, or remove this option.')).not.toBeInTheDocument();
    expect(api).not.toHaveBeenCalled();
  });

  it('cuts a pasted write-up to 1,000 characters, tells the planner, and saves what fits', async () => {
    const { user, api } = renderOptions();

    await user.click(screen.getByRole('button', { name: 'Add option' }));
    await user.paste(VENDOR_WRITE_UP);

    expect(codePoints(field(3).value)).toBe(1000);
    expect(screen.getByText('Pasted text was cut to fit 1,000 characters.')).toBeInTheDocument();
    expect(screen.getByText('1,000 / 1,000')).toBeInTheDocument();

    await user.click(document.body);
    expect(codePoints(sent(api)[2].text)).toBe(1000);
  });

  it('keeps line breaks in a pasted pros-and-cons list', async () => {
    const { user, api } = renderOptions();
    const prosAndCons = 'Use the existing vendor\nPros: knows the brand, no ramp-up\nCons: about 10% more expensive';

    await user.click(screen.getByRole('button', { name: 'Add option' }));
    await user.paste(prosAndCons);
    await user.click(document.body);

    expect(sent(api)[2].text).toBe(prosAndCons);
  });

  it('flags a blank option when the planner picks it as the one to commit to', async () => {
    const { user } = renderOptions();

    await user.click(screen.getByRole('button', { name: 'Add option' }));
    await user.click(screen.getByRole('radio', { name: 'Choose option 3' }));

    expect(screen.getByText('Enter text, or remove this option.')).toBeInTheDocument();
  });

  it('keeps the last saved text of an option the planner has cleared while changing the choice', async () => {
    const { user, api } = renderOptions();

    await user.clear(field(1));
    await user.click(screen.getByRole('radio', { name: 'Choose option 2' }));

    expect(sent(api).map((o) => o.text)).toEqual([
      'Use the existing vendor for all Q4 creative',
      'Split video work with a new agency, vendor keeps static and display',
    ]);
    expect(sent(api).map((o) => o.isSelected)).toEqual([false, true]);
    expect(field(1).value).toBe('');
  });

  it('does not wipe what the planner is typing when an earlier save comes back', async () => {
    let finishSave: () => void = () => {};
    const api = jest.fn(() => new Promise<void>((resolve) => { finishSave = resolve; }));
    const { user } = renderOptions(api);

    await user.click(field(1));
    await user.keyboard('{End} and display');
    await user.click(field(2));
    await user.keyboard('{End}, review after the first week');

    await act(async () => finishSave());

    await waitFor(() => expect(api).toHaveBeenCalledTimes(1));
    expect(field(1).value).toBe('Use the existing vendor for all Q4 creative and display');
    expect(field(2).value).toBe(
      'Split video work with a new agency, vendor keeps static and display, review after the first week',
    );
  });

  it('keeps the planner’s text when the save fails', async () => {
    const api = jest.fn().mockRejectedValue(new Error('Network Error'));
    const { user } = renderOptions(api);

    await user.click(field(2));
    await user.keyboard('{End} (backup plan)');
    await user.click(document.body);

    await waitFor(() => expect(api).toHaveBeenCalledTimes(1));
    expect(field(2).value).toBe('Split video work with a new agency, vendor keeps static and display (backup plan)');
  });
});
