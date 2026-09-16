import { render, screen } from '@testing-library/react';
import DedupeToastContent from '@/components/state-feedback/DedupeToastContent';

describe('DedupeToastContent', () => {
  it('renders the message and toast type test id', () => {
    render(<DedupeToastContent message="Network error" count={1} type="error" />);

    expect(screen.getByTestId('toast-error')).toBeInTheDocument();
    expect(screen.getAllByText('Network error').length).toBeGreaterThan(0);
  });

  it('does not show count badge when count is 1', () => {
    render(<DedupeToastContent message="Network error" count={1} type="error" />);

    expect(screen.queryByTestId('toast-count-badge')).not.toBeInTheDocument();
  });

  it('shows count badge when count is greater than 1', () => {
    render(<DedupeToastContent message="Network error" count={4} type="error" />);

    expect(screen.getByTestId('toast-count-badge')).toHaveTextContent('×4');
    expect(screen.getByTestId('toast-live-announcement')).toHaveTextContent(
      'Network error. Repeated 4 times.',
    );
  });

  it('announces the message without a repeat count when count is 1', () => {
    render(<DedupeToastContent message="Network error" count={1} type="error" />);

    const toast = screen.getByTestId('toast-error');
    expect(toast).toHaveAttribute('aria-live', 'polite');
    expect(screen.getByTestId('toast-live-announcement')).toHaveTextContent('Network error');
    expect(screen.getByTestId('toast-live-announcement')).not.toHaveTextContent('Repeated');
  });

  it('uses type-specific test id for success toasts', () => {
    render(<DedupeToastContent message="Saved" count={2} type="success" />);

    expect(screen.getByTestId('toast-success')).toBeInTheDocument();
    expect(screen.getByTestId('toast-count-badge')).toHaveTextContent('×2');
  });
});
