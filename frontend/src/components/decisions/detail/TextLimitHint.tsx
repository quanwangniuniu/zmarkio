'use client';

import { Info } from 'lucide-react';
import { formatCounter, type LengthState } from '@/components/decisions/decisionValidation';

interface Props {
  id: string;
  length: LengthState;
  message: string | null;
}

export default function TextLimitHint({ id, length, message }: Props) {
  return (
    <div id={id} className="mt-1 flex items-start justify-between gap-3 text-[12px]">
      <span aria-live="polite" className="flex min-w-0 items-start gap-1 text-gray-500">
        {message && (
          <>
            <Info aria-hidden="true" className="mt-[1px] h-3.5 w-3.5 shrink-0" />
            <span>{message}</span>
          </>
        )}
      </span>
      {length.showCounter && (
        <span className="shrink-0 tabular-nums text-gray-400">{formatCounter(length)}</span>
      )}
    </div>
  );
}
