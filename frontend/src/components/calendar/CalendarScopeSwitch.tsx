'use client';

import { motion } from 'framer-motion';
import type { BookingScope } from '@/lib/bookingLinkScope';

type CalendarScopeSwitchProps = {
  scope: BookingScope;
  onChange: (scope: BookingScope) => void;
  testIdPrefix?: string;
};

export function CalendarScopeSwitch({
  scope,
  onChange,
  testIdPrefix = 'calendar-scope',
}: CalendarScopeSwitchProps) {
  return (
    <div
      className="relative grid grid-cols-2 rounded-lg bg-gray-100 p-1"
      role="tablist"
      data-testid={testIdPrefix}
    >
      <motion.span
        aria-hidden
        className="pointer-events-none absolute bottom-1 left-1 top-1 w-[calc(50%-4px)] rounded-md bg-white shadow-sm"
        animate={{ x: scope === 'personal' ? '100%' : '0%' }}
        transition={{ type: 'spring', stiffness: 380, damping: 32 }}
      />
      {(['team', 'personal'] as BookingScope[]).map((value) => (
        <button
          key={value}
          type="button"
          role="tab"
          aria-selected={scope === value}
          onClick={() => onChange(value)}
          data-testid={`${testIdPrefix}-${value}`}
          className={`relative z-10 rounded-md px-2 py-1.5 text-xs font-medium transition-colors duration-200 ${
            scope === value
              ? 'text-[#0E8A96]'
              : 'text-gray-500 hover:text-gray-800'
          }`}
        >
          {value === 'team' ? 'Team' : 'Personal'}
        </button>
      ))}
    </div>
  );
}
