import React from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import '@testing-library/jest-dom';

import QualityRatingForm from '@/components/csm/quality/QualityRatingForm';
import type { ConversationQualityReview } from '@/types/csmQuality';

const existing: ConversationQualityReview = {
  id: 7,
  conversation: 12,
  rating: 'needs_improvement',
  rating_display: 'Needs Improvement',
  comment: 'Slow first reply.',
  reviewer: 3,
  reviewer_name: 'Grace Hopper',
  reviewed_at: '2026-03-04T10:30:00Z',
  agent_user: 9,
  agent_name: 'Ada L.',
  queue: 1,
  organisation: 1,
};

describe('QualityRatingForm — AC3 annotate with a rating and comment', () => {
  it('submits the chosen rating and comment', () => {
    const onSubmit = jest.fn();
    render(<QualityRatingForm existingReview={null} saving={false} onSubmit={onSubmit} />);

    fireEvent.click(screen.getByRole('radio', { name: 'Poor' }));
    fireEvent.change(screen.getByLabelText('Comment'), {
      target: { value: 'Missed the refund policy.' },
    });
    fireEvent.click(screen.getByRole('button', { name: /save annotation/i }));

    expect(onSubmit).toHaveBeenCalledWith('poor', 'Missed the refund policy.');
  });

  it('cannot submit before a rating is chosen', () => {
    const onSubmit = jest.fn();
    render(<QualityRatingForm existingReview={null} saving={false} onSubmit={onSubmit} />);

    expect(screen.getByRole('button', { name: /save annotation/i })).toBeDisabled();
  });

  it('seeds from an existing review and shows who reviewed it and when', () => {
    render(<QualityRatingForm existingReview={existing} saving={false} onSubmit={jest.fn()} />);

    expect(screen.getByRole('radio', { name: 'Needs Improvement' })).toHaveAttribute(
      'aria-checked',
      'true',
    );
    expect(screen.getByLabelText('Comment')).toHaveValue('Slow first reply.');
    expect(screen.getByText(/Grace Hopper/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /update annotation/i })).toBeInTheDocument();
  });

  it('disables the button while saving', () => {
    render(<QualityRatingForm existingReview={existing} saving onSubmit={jest.fn()} />);

    expect(screen.getByRole('button', { name: /saving/i })).toBeDisabled();
  });
});
