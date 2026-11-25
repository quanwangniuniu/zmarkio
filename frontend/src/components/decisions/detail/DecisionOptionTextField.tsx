'use client';

import { useEffect, useState } from 'react';
import TextLimitHint from '@/components/decisions/detail/TextLimitHint';
import { useLimitedText } from '@/components/decisions/detail/hooks/useLimitedText';
import { COUNTER_THRESHOLDS, DECISION_LIMITS } from '@/components/decisions/decisionValidation';

interface Props {
  id: string;
  value: string;
  placeholder: string;
  ariaLabel: string;
  error: string | null;
  focusOnMount?: boolean;
  onChange: (text: string) => void;
  onBlur: () => void;
}

export default function DecisionOptionTextField({
  id,
  value,
  placeholder,
  ariaLabel,
  error,
  focusOnMount,
  onChange,
  onBlur,
}: Props) {
  const [focused, setFocused] = useState(false);
  const hintId = `${id}-hint`;
  const text = useLimitedText({
    value,
    onChange,
    max: DECISION_LIMITS.optionText,
    counterFrom: COUNTER_THRESHOLDS.optionText,
    maxHeight: 180,
  });
  const { ref } = text.textareaProps;

  useEffect(() => {
    if (focusOnMount) ref.current?.focus();
  }, [focusOnMount, ref]);

  const showHint = !!error || (focused && (text.length.showCounter || text.message != null));

  return (
    <div className="min-w-0 flex-1">
      <textarea
        {...text.textareaProps}
        id={id}
        onFocus={() => setFocused(true)}
        onBlur={() => {
          setFocused(false);
          text.clearNotice();
          onBlur();
        }}
        placeholder={placeholder}
        aria-label={ariaLabel}
        aria-invalid={error ? true : undefined}
        aria-describedby={showHint ? hintId : undefined}
        className={`block min-h-[32px] w-full resize-none rounded-md border bg-white px-2.5 py-1.5 text-sm leading-5 text-gray-900 placeholder:text-gray-400 outline-none transition ${
          error
            ? 'border-rose-400 focus:border-rose-500 focus:ring-2 focus:ring-rose-200'
            : 'border-gray-200 focus:border-[#3CCED7] focus:ring-2 focus:ring-[#3CCED7]/30'
        }`}
      />
      {showHint && (
        <TextLimitHint id={hintId} length={text.length} message={text.message} error={error} />
      )}
    </div>
  );
}
