import { render, screen } from '@testing-library/react';
import EmptyState from '@/components/state-feedback/EmptyState';


describe('EmptyState', () => {


  it('does not render the CTA when onAction is missing', () => {

    render(<EmptyState
            icon={<span>icon</span>}
            title="No tasks yet"
            actionLabel="Add the first task"
          />)
          
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
  });



  it('renders the CTA when both actionLabel and onAction exist', () => {

    render(<EmptyState
            icon={<span>icon</span>}
            title="No tasks yet"
            actionLabel="Add the first task"
            onAction={()=>{}}
          />)
          
    expect(screen.getByRole('button', { name: 'Add the first task' })).toBeInTheDocument();
  });


  it('does not render the CTA when actionLabel is missing', () => {

    render(<EmptyState
            icon={<span>icon</span>}
            title="No tasks yet"
            onAction={() => {}}
          />)
          
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
  });

  it('applies actionClassName to the CTA button', () => {
    render(<EmptyState
            icon={<span>icon</span>}
            title="Select a conversation"
            actionLabel="Browse conversations"
            onAction={() => {}}
            actionClassName="md:hidden"
          />)

    expect(screen.getByRole('button', { name: 'Browse conversations' })).toHaveClass('md:hidden');
  });
});