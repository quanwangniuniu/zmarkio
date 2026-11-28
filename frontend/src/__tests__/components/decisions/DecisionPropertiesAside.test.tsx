import { useState } from 'react';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import '@testing-library/jest-dom';
import DecisionPropertiesAside from '@/components/decisions/detail/DecisionPropertiesAside';

// Radix Select uses pointer capture and scrollIntoView, which jsdom does not implement.
beforeAll(() => {
  Element.prototype.hasPointerCapture ??= () => false;
  Element.prototype.releasePointerCapture ??= () => {};
  Element.prototype.scrollIntoView ??= () => {};
});

function renderProperties(confidenceScore: number | null) {
  const api = jest.fn().mockResolvedValue(undefined);
  function Page() {
    const [score, setScore] = useState(confidenceScore);
    return (
      <DecisionPropertiesAside
        status="DRAFT"
        riskLevel="MEDIUM"
        confidenceScore={score}
        plannedDecisionDate="2026-12-01T00:00:00Z"
        editable
        onConfidenceChange={async (next) => {
          await api(next);
          setScore(next);
        }}
      />
    );
  }
  const user = userEvent.setup();
  render(<Page />);
  return { user, api, picker: screen.getByRole('combobox', { name: 'Confidence score' }) };
}

describe('Decision confidence — what a planner sees and does', () => {
  it('flags a score of 87 saved by the old free-text field and lets the planner fix it', async () => {
    const { user, api, picker } = renderProperties(87);

    expect(picker).toHaveTextContent('Choose 1–5');
    expect(picker).toHaveAttribute('aria-invalid', 'true');
    expect(screen.getByText('Saved value 87 is out of range. Choose 1–5.')).toBeInTheDocument();

    await user.click(picker);
    const choices = within(screen.getByRole('listbox')).getAllByRole('option').map((o) => o.textContent);
    expect(choices).toEqual(['Unassigned', '1', '2', '3', '4', '5']);
    await user.click(screen.getByRole('option', { name: '4' }));

    expect(api).toHaveBeenCalledWith(4);
    expect(screen.queryByText(/is out of range/)).not.toBeInTheDocument();
    expect(picker).toHaveTextContent('4');
    expect(picker).not.toHaveAttribute('aria-invalid');
  });

  it('also flags a score of 0', () => {
    renderProperties(0);
    expect(screen.getByText('Saved value 0 is out of range. Choose 1–5.')).toBeInTheDocument();
  });

  it('shows a valid score as is, with no error', () => {
    const { picker } = renderProperties(3);
    expect(picker).toHaveTextContent('3');
    expect(screen.queryByText(/is out of range/)).not.toBeInTheDocument();
  });

  it('lets the planner clear the score back to Unassigned', async () => {
    const { user, api, picker } = renderProperties(3);

    await user.click(picker);
    await user.click(screen.getByRole('option', { name: 'Unassigned' }));

    expect(api).toHaveBeenCalledWith(null);
    expect(picker).toHaveTextContent('Unassigned');
  });
});
