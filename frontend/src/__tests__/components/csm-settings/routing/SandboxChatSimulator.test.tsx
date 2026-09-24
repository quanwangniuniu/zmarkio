import { render, screen, fireEvent } from '@testing-library/react';
import '@testing-library/jest-dom';
import SandboxChatSimulator from '@/components/csm-settings/sandbox/SandboxChatSimulator';

jest.mock('@/components/csm/conversations/ConversationThread', () => ({
  ConversationThread: ({ messages }: { messages: { id: number; content: string }[] }) => (
    <div data-testid="thread">{messages.map((m) => <p key={m.id}>{m.content}</p>)}</div>
  ),
}));

describe('SandboxChatSimulator', () => {
  it('sends on Enter and clears the draft', () => {
    const onSend = jest.fn();
    render(<SandboxChatSimulator messages={[]} disabled={false} onSend={onSend} />);
    const box = screen.getByLabelText('Customer message');
    fireEvent.change(box, { target: { value: 'I need a refund' } });
    fireEvent.keyDown(box, { key: 'Enter' });
    expect(onSend).toHaveBeenCalledWith('I need a refund');
    expect(box).toHaveValue('');
  });

  it('keeps Shift+Enter as a newline', () => {
    const onSend = jest.fn();
    render(<SandboxChatSimulator messages={[]} disabled={false} onSend={onSend} />);
    const box = screen.getByLabelText('Customer message');
    fireEvent.change(box, { target: { value: 'line one' } });
    fireEvent.keyDown(box, { key: 'Enter', shiftKey: true });
    expect(onSend).not.toHaveBeenCalled();
  });

  it('shows the isolation banner and blocks sending when disabled', () => {
    const onSend = jest.fn();
    render(
      <SandboxChatSimulator
        messages={[]}
        disabled
        disabledReason="Select an experience group first"
        onSend={onSend}
      />,
    );
    expect(screen.getByText(/nothing is sent or saved/i)).toBeInTheDocument();
    expect(screen.getByLabelText('Customer message')).toHaveAttribute(
      'placeholder', 'Select an experience group first',
    );
    expect(screen.getByRole('button', { name: /send test message/i })).toBeDisabled();
  });
});
