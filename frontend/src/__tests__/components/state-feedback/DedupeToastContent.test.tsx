import { render, screen } from '@testing-library/react';
import DedupeToastContent from '@/components/state-feedback/DedupeToastContent';

describe('DedupeToastContent', () => {
  it('renders the message and toast type test id', () => {
    render(<DedupeToastContent message="Network error" count={1} type="error" />);

    expect(screen.getByTestId('toast-error')).toBeInTheDocument();
    expect(screen.getByText('Network error')).toBeInTheDocument();
  });

  it('does not show count badge when count is 1', () => {
    render(<DedupeToastContent message="Network error" count={1} type="error" />);

    expect(screen.queryByTestId('toast-count-badge')).not.toBeInTheDocument();
  });

  it('shows count badge when count is greater than 1', () => {
    render(<DedupeToastContent message="Network error" count={4} type="error" />);

    expect(screen.getByTestId('toast-count-badge')).toHaveTextContent('×4');
  });

  it('uses type-specific test id for success toasts', () => {
    render(<DedupeToastContent message="Saved" count={2} type="success" />);

    expect(screen.getByTestId('toast-success')).toBeInTheDocument();
    expect(screen.getByTestId('toast-count-badge')).toHaveTextContent('×2');
  });
});
