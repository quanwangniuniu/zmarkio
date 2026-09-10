import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import Toggle from '@/components/ui/Toggle';
import StatusBadge from '@/components/ui/StatusBadge';
import AutoResizeTextarea from '@/components/ui/AutoResizeTextarea';

jest.mock('@/hooks/useAutoResizeTextarea', () => ({
  useAutoResizeTextarea: () => ({
    textareaRef: { current: null },
    resizeTextarea: jest.fn(),
  }),
}));

describe('Toggle', () => {
  it('toggles when enabled and ignores clicks when disabled', async () => {
    const user = userEvent.setup();
    const onChange = jest.fn();
    const { rerender } = render(
      <Toggle id="t1" label="Notify" checked={false} onChange={onChange} description="desc" />
    );

    await user.click(screen.getByRole('switch'));
    expect(onChange).toHaveBeenCalledWith(true);

    rerender(
      <Toggle id="t1" label="Notify" checked disabled onChange={onChange} description="desc" />
    );
    onChange.mockClear();
    await user.click(screen.getByRole('switch'));
    expect(onChange).not.toHaveBeenCalled();
  });
});

describe('StatusBadge', () => {
  it('maps known and unknown statuses', () => {
    const { rerender } = render(<StatusBadge status="active" />);
    expect(screen.getByText('active').className).toContain('status-badge-active');
    rerender(<StatusBadge status="mystery" />);
    expect(screen.getByText('mystery').className).toContain('status-badge-default');
  });
});

describe('AutoResizeTextarea', () => {
  it('renders and forwards input', async () => {
    const user = userEvent.setup();
    const onInput = jest.fn();
    render(<AutoResizeTextarea value="hello" onInput={onInput} aria-label="notes" />);
    await user.type(screen.getByLabelText('notes'), '!');
    expect(onInput).toHaveBeenCalled();
  });
});
