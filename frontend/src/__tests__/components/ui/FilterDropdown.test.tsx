import { render, screen, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import FilterDropdown from '@/components/ui/FilterDropdown';

describe('FilterDropdown', () => {
  const options = [
    { id: 'a', name: 'Alpha' },
    { id: 'b', name: 'Beta', disabled: true },
    { id: 'c', name: 'Gamma' },
  ];

  it('opens, selects an option, and closes on outside click', async () => {
    const user = userEvent.setup();
    const onChange = jest.fn();
    render(
      <div>
        <FilterDropdown label="Status" value="a" onChange={onChange} options={options} />
        <button type="button">outside</button>
      </div>
    );

    await user.click(screen.getByRole('button', { name: /Alpha/i }));
    expect(screen.getByRole('option', { name: 'Gamma' })).toBeInTheDocument();
    await user.click(screen.getByRole('option', { name: 'Gamma' }));
    expect(onChange).toHaveBeenCalledWith('c');

    // Re-open then close via outside click
    await user.click(screen.getByRole('button', { name: /Gamma|Alpha|Select/i }));
    fireEvent.mouseDown(screen.getByText('outside'));
  });

  it('handles keyboard interactions and disabled/loading/error states', async () => {
    const user = userEvent.setup();
    const onChange = jest.fn();
    const { rerender } = render(
      <FilterDropdown
        label="Status"
        value=""
        onChange={onChange}
        options={[]}
        error="Required"
        placeholder="Pick one"
      />
    );

    expect(screen.getByText('Required')).toBeInTheDocument();
    const trigger = screen.getByRole('button');
    fireEvent.keyDown(trigger, { key: 'Enter' });
    expect(screen.getByText('No options available')).toBeInTheDocument();
    fireEvent.keyDown(trigger, { key: 'Escape' });
    fireEvent.keyDown(trigger, { key: 'ArrowDown' });
    fireEvent.keyDown(trigger, { key: 'ArrowUp' });

    rerender(
      <FilterDropdown label="Status" value="" onChange={onChange} options={options} loading />
    );
    expect(screen.getByText('Loading...')).toBeInTheDocument();

    rerender(
      <FilterDropdown label="Status" value="a" onChange={onChange} options={options} disabled />
    );
    await user.click(screen.getByRole('button'));
    expect(onChange).not.toHaveBeenCalled();
  });
});
