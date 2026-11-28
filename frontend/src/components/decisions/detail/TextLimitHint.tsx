'use client';

import { AlertCircle, Info } from 'lucide-react';
import { formatCounter, type LengthState } from '@/components/decisions/decisionValidation';

interface Props {
  id: string;
  length: LengthState;
  message: string | null;
  error?: string | null;
}

export default function TextLimitHint({ id, length, message, error }: Props) {
  return (
    <div id={id} className="mt-1 flex items-start justify-between gap-3 text-[12px]">
      <span
        aria-live="polite"
        className={`flex min-w-0 items-start gap-1 ${error ? 'text-rose-600' : 'text-gray-500'}`}
      >
        {(error || message) && (
          <>
            {error ? (
              <AlertCircle aria-hidden="true" className="mt-[1px] h-3.5 w-3.5 shrink-0" />
            ) : (
              <Info aria-hidden="true" className="mt-[1px] h-3.5 w-3.5 shrink-0" />
            )}
            <span>{error || message}</span>
          </>
        )}
      </span>
      {length.showCounter && (
        <span className="shrink-0 tabular-nums text-gray-400">{formatCounter(length)}</span>
      )}
    </div>
  );
}
