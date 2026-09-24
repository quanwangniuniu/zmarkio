import { render, screen, fireEvent, within } from '@testing-library/react';
import '@testing-library/jest-dom';
import RoutingTracePanel from '@/components/csm-settings/sandbox/RoutingTracePanel';
import { FALLBACK_TRACE, VOCABULARY, makeTrace } from './__mocks__/routingFixtures';

const lookups = {
  vocabulary: VOCABULARY,
  channelNames: new Map<number, string>(),
  organisationNames: new Map<number, string>(),
};

function renderPanel(props: Partial<React.ComponentProps<typeof RoutingTracePanel>> = {}) {
  const onRerun = jest.fn();
  render(
    <RoutingTracePanel
      traces={[]}
      customerMessages={[]}
      meta={null}
      evaluating={false}
      error={null}
      lookups={lookups}
      onRerun={onRerun}
      {...props}
    />,
  );
  return { onRerun };
}

describe('RoutingTracePanel', () => {
  it('prompts for a message when there is no trace yet', () => {
    renderPanel();
    expect(screen.getByText(/send a test message/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /re-run/i })).toBeDisabled();
  });

  it('shows each evaluated rule, its condition results and the action', () => {
    renderPanel({ traces: [makeTrace()], customerMessages: ['I want a refund'] });

    const steps = screen.getAllByTestId('trace-step');
    expect(steps).toHaveLength(2);
    expect(within(steps[0]).getByText('Matched')).toBeInTheDocument();
    expect(within(steps[0]).getByText(/Latest customer message contains any of “refund”/)).toBeInTheDocument();
    expect(within(steps[0]).getByText(/Matched: refund/)).toBeInTheDocument();
    expect(within(steps[0]).getByLabelText('passed')).toBeInTheDocument();
    expect(within(steps[0]).getByText(/route to/i)).toHaveTextContent('Billing');
    expect(within(steps[1]).getByText('Not reached')).toBeInTheDocument();
    expect(screen.getByTestId('trace-outcome')).toHaveTextContent('Billing');
  });

  it('shows the fallback step and flags when the outcome changed between messages', () => {
    renderPanel({
      traces: [makeTrace(), FALLBACK_TRACE],
      customerMessages: ['refund', 'Hello'],
    });

    const turns = screen.getAllByTestId('trace-turn');
    // Newest message first and expanded.
    expect(within(turns[0]).getByText('Message 2')).toBeInTheDocument();
    expect(within(turns[0]).getByText(/Changed from Billing/)).toBeInTheDocument();
    expect(within(turns[0]).getByTestId('trace-fallback')).toHaveTextContent("Channel's default queue → Frontline");
    expect(within(turns[0]).getByLabelText('failed')).toBeInTheDocument();
    expect(within(turns[1]).queryByTestId('trace-step')).not.toBeInTheDocument();

    fireEvent.click(within(turns[1]).getByRole('button', { expanded: false }));
    expect(within(turns[1]).getAllByTestId('trace-step')).toHaveLength(2);
  });

  it('shows sandbox and trace warnings, and re-runs on demand', () => {
    const { onRerun } = renderPanel({
      traces: [makeTrace({ warnings: ["Rule 'Old' matched but has no target queue; it was skipped."] })],
      customerMessages: ['refund'],
      meta: {
        experience_group: { id: 1, name: 'VIP', status: 'DRAFT' },
        support_channel: null,
        rule_count: 2,
        warnings: ["Channel 'Inbox' is inactive."],
      },
    });
    expect(screen.getByText("Channel 'Inbox' is inactive.")).toBeInTheDocument();
    expect(screen.getByText(/has no target queue/)).toBeInTheDocument();
    expect(screen.getByText(/2 rule\(s\) evaluated/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /re-run/i }));
    expect(onRerun).toHaveBeenCalled();
  });
});
