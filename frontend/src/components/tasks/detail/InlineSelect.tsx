'use client';

import * as Select from '@radix-ui/react-select';
import { Check, ChevronDown } from 'lucide-react';
import type { ReactNode } from 'react';

export interface InlineSelectOption {
  value: string;
  label: string;
  leading?: ReactNode;
  sub?: string;
}

interface Props {
  value: string;
  onValueChange: (v: string) => void;
  options: InlineSelectOption[];
  placeholder?: string;
  disabled?: boolean;
  ariaLabel?: string;
  invalid?: boolean;
  ariaDescribedBy?: string;
}

export default function InlineSelect({
  value,
  onValueChange,
  options,
  placeholder,
  disabled,
  ariaLabel,
  invalid,
  ariaDescribedBy,
}: Props) {
  return (
    <Select.Root value={value} onValueChange={onValueChange} disabled={disabled}>
      <Select.Trigger
        aria-label={ariaLabel}
        aria-invalid={invalid || undefined}
        aria-describedby={ariaDescribedBy}
        className={`inline-flex min-w-0 w-full items-center justify-between gap-2 rounded-md border bg-white px-2.5 py-1.5 text-sm text-gray-900 outline-none transition focus:ring-2 disabled:cursor-not-allowed disabled:bg-gray-50 disabled:text-gray-500 [&>span:first-child]:min-w-0 [&>span:first-child]:truncate ${
          invalid
            ? 'border-rose-400 focus:border-rose-500 focus:ring-rose-200'
            : 'border-gray-200 hover:border-gray-300 focus:border-[#3CCED7] focus:ring-[#3CCED7]/30'
        }`}
      >
        <Select.Value placeholder={placeholder || 'Select…'} />
        <Select.Icon className="shrink-0 text-gray-400">
          <ChevronDown className="h-3.5 w-3.5" />
        </Select.Icon>
      </Select.Trigger>
      <Select.Portal>
        <Select.Content
          position="popper"
          sideOffset={4}
          className="z-[1000] min-w-[var(--radix-select-trigger-width)] max-w-[calc(100vw-1rem)] overflow-hidden rounded-lg bg-white shadow-lg ring-1 ring-gray-100 data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:zoom-in-95"
        >
          <Select.Viewport className="max-h-56 overflow-y-auto py-1">
            {options.map((opt) => (
              <Select.Item
                key={opt.value}
                value={opt.value}
                className="relative flex cursor-pointer select-none items-center gap-2 px-2.5 py-2 pr-8 text-sm text-gray-800 outline-none transition hover:bg-gray-50 data-[highlighted]:bg-gray-50 data-[state=checked]:text-gray-900"
              >
                {opt.leading && <span className="shrink-0">{opt.leading}</span>}
                <div className="min-w-0 flex-1">
                  <Select.ItemText>{opt.label}</Select.ItemText>
                  {opt.sub && (
                    <span className="ml-2 break-words text-[11px] text-gray-400">{opt.sub}</span>
                  )}
                </div>
                <Select.ItemIndicator className="absolute right-2 inline-flex items-center">
                  <Check className="h-3.5 w-3.5 text-[#3CCED7]" />
                </Select.ItemIndicator>
              </Select.Item>
            ))}
          </Select.Viewport>
        </Select.Content>
      </Select.Portal>
    </Select.Root>
  );
}

export function UserInitialsAvatar({ name }: { name: string }) {
  const initials = name
    .split(/[\s@.]+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((s) => s[0]?.toUpperCase() || '')
    .join('') || '?';
  return (
    <span className="inline-flex h-5 w-5 items-center justify-center rounded-full bg-gradient-to-br from-[#3CCED7] to-[#A6E661] text-[9px] font-semibold text-white">
      {initials}
    </span>
  );
}
