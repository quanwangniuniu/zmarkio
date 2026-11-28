import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import '@testing-library/jest-dom';
import DecisionDetailHeader from '@/components/decisions/detail/DecisionDetailHeader';

jest.mock('@/lib/buildUrl', () => ({ useBuildUrl: () => (path: string) => path }));

const codePoints = (text: string) => Array.from(text).length;

// Copied straight from a media planning meeting's notes, line breaks included.
const MEETING_NOTES =
  'Q4 media budget decision:\r\n' +
  '- after reviewing the September results with the paid social and search teams, we agreed to move part of the remaining Meta budget into Google Search for the holiday period\r\n' +
  '- keep TikTok at its current level\r\n' +
  '- revisit the split in early December once the Black Friday numbers are in';

const LONG_TITLE =
  'Shift 30% of the remaining Q4 Meta budget into Google Search and YouTube for the Black Friday to Boxing Day period, ' +
  'keep TikTok flat, and review the split with the paid social and search leads once the first December results are in';

// For exact-length cases: the same real title, carried on into the planner's own follow-up note.
const titleOfLength = (length: number) =>
  Array.from(`${LONG_TITLE}. Confirm with finance before the 1 December cut-off.`).slice(0, length).join('');

function renderHeader(props: Partial<React.ComponentProps<typeof DecisionDetailHeader>> = {}) {
  const onTitleSave = jest.fn().mockResolvedValue(undefined);
  const user = userEvent.setup();
  render(
    <DecisionDetailHeader
      projectId={12}
      projectName="AU Paid Media"
      projectSeq={48}
      title=""
      status="DRAFT"
      editable
      onTitleSave={onTitleSave}
      {...props}
    />,
  );
  return { user, onTitleSave, title: screen.getByRole('textbox', { name: 'Decision title' }) as HTMLTextAreaElement };
}

describe('Decision title — what a planner does with it', () => {
  it('pastes a whole paragraph of meeting notes: keeps one line, cuts it to 255 and says so', async () => {
    const { user, onTitleSave, title } = renderHeader();

    await user.click(title);
    await user.paste(MEETING_NOTES);

    expect(title.value).not.toMatch(/[\r\n]/);
    expect(codePoints(title.value)).toBe(255);
    expect(title.value.startsWith('Q4 media budget decision: - after reviewing')).toBe(true);
    expect(screen.getByText('Pasted text was cut to fit 255 characters.')).toBeInTheDocument();
    expect(screen.getByText('255 / 255')).toBeInTheDocument();

    await user.click(document.body);
    expect(onTitleSave).toHaveBeenCalledTimes(1);
    expect(codePoints(onTitleSave.mock.calls[0][0])).toBeLessThanOrEqual(255);
  });

  it('pastes into the middle of a long title without losing the words after the cursor', async () => {
    const { user, title } = renderHeader({ title: LONG_TITLE });
    const cursor = LONG_TITLE.indexOf(' for the Black Friday');
    const room = 255 - codePoints(LONG_TITLE);

    await user.click(title);
    title.setSelectionRange(cursor, cursor);
    await user.paste(' and Performance Max campaigns across AU and NZ');

    expect(codePoints(title.value)).toBe(255);
    expect(title.value.endsWith(LONG_TITLE.slice(cursor))).toBe(true);
    expect(title.value.slice(cursor, cursor + room)).toBe(' and Performance Max campaigns across AU and NZ'.slice(0, room));
    expect(title.selectionStart).toBe(cursor + room);
  });

  it('replaces a selected phrase with pasted text when there is room', async () => {
    const { user, title } = renderHeader({ title: LONG_TITLE });
    const start = LONG_TITLE.indexOf('30%');

    await user.click(title);
    title.setSelectionRange(start, start + '30%'.length);
    await user.paste('25%');

    expect(title.value).toBe(LONG_TITLE.replace('30%', '25%'));
    expect(screen.queryByText(/Pasted text was cut/)).not.toBeInTheDocument();
  });

  it('never splits an emoji when the paste only partly fits', async () => {
    const base = titleOfLength(253);
    const { user, title } = renderHeader({ title: base });

    await user.click(title);
    title.setSelectionRange(base.length, base.length);
    await user.paste('🎄🎁 Christmas push');

    expect(title.value).toBe(`${base}🎄🎁`);
    expect(codePoints(title.value)).toBe(255);
  });

  it('blocks typing at the limit but still lets the planner delete', async () => {
    const full = titleOfLength(255);
    const { user, title } = renderHeader({ title: full });

    await user.click(title);
    await user.keyboard('{End} (urgent)');
    expect(title.value).toBe(full);
    expect(screen.getByText('Character limit reached.')).toBeInTheDocument();

    await user.keyboard('{Backspace}{Backspace}{Backspace}');
    expect(codePoints(title.value)).toBe(252);
    expect(screen.queryByText('Character limit reached.')).not.toBeInTheDocument();
  });

  it('shows the counter only when the title gets close to the limit', async () => {
    const { user, title } = renderHeader({ title: titleOfLength(220) });

    await user.click(title);
    expect(screen.queryByText(/\/ 255$/)).not.toBeInTheDocument();

    await user.keyboard('{End} across AU and NZ');
    expect(screen.getByText('237 / 255')).toBeInTheDocument();
  });

  it('saves on Enter and sends the trimmed title once', async () => {
    const { user, onTitleSave, title } = renderHeader({ title: 'Q4 budget split' });

    await user.click(title);
    await user.keyboard('{End} for Black Friday  {Enter}');

    expect(onTitleSave).toHaveBeenCalledTimes(1);
    expect(onTitleSave).toHaveBeenCalledWith('Q4 budget split for Black Friday');
    expect(title).not.toHaveFocus();
  });

  it('throws away the edit on Escape and saves nothing', async () => {
    const { user, onTitleSave, title } = renderHeader({ title: 'Q4 budget split' });

    await user.click(title);
    await user.keyboard('{End} — draft{Escape}');

    expect(title.value).toBe('Q4 budget split');
    expect(onTitleSave).not.toHaveBeenCalled();
  });

  it('does not send a request when the planner only clicks in and out', async () => {
    const { user, onTitleSave, title } = renderHeader({ title: 'Q4 budget split' });

    await user.click(title);
    await user.keyboard('{End}   ');
    await user.click(document.body);

    expect(onTitleSave).not.toHaveBeenCalled();
  });

  it('keeps the typed title when the save fails, so the planner can try again', async () => {
    const onTitleSave = jest.fn().mockRejectedValue(new Error('Network Error'));
    const { user, title } = renderHeader({ title: 'Q4 budget split', onTitleSave });

    await user.click(title);
    await user.keyboard('{End} v2');
    await user.click(document.body);

    await waitFor(() => expect(onTitleSave).toHaveBeenCalledTimes(1));
    expect(title.value).toBe('Q4 budget split v2');
  });

  it('shows the server field error under the title and clears it as soon as the planner edits', async () => {
    const onErrorDismiss = jest.fn();
    const { user, title } = renderHeader({
      title: 'Q4 budget split',
      error: 'Title must be 255 characters or fewer.',
      onErrorDismiss,
    });

    expect(title).toHaveAttribute('aria-invalid', 'true');
    expect(screen.getByText('Title must be 255 characters or fewer.')).toBeInTheDocument();

    await user.click(title);
    await user.keyboard('{Backspace}');
    expect(onErrorDismiss).toHaveBeenCalled();
  });
});
