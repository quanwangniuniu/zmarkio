'use client';

import * as Dialog from '@radix-ui/react-dialog';
import { X } from 'lucide-react';
import type { ReactNode } from 'react';

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  subtitle?: string;
  width?: string;
  elevated?: boolean;
  children: ReactNode;
  onPointerDownOutside?: (e: Event) => void;
  onInteractOutside?: (e: Event) => void;
  onOpenAutoFocus?: (e: Event) => void;
}

export default function BrandDialog({
  open,
  onOpenChange,
  title,
  subtitle,
  width = 'max-w-lg',
  elevated = false,
  children,
  onPointerDownOutside,
  onInteractOutside,
  onOpenAutoFocus,
}: Props) {
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className={`fixed inset-0 ${elevated ? 'z-[210]' : 'z-40'} bg-black/30 backdrop-blur-[1px] data-[state=open]:animate-in data-[state=open]:fade-in-0`} />
        <Dialog.Content
          onPointerDownOutside={onPointerDownOutside as any}
          onInteractOutside={onInteractOutside as any}
          onOpenAutoFocus={onOpenAutoFocus as any}
          className={`fixed left-1/2 top-1/2 ${elevated ? 'z-[220]' : 'z-50'} flex max-h-[calc(100dvh-1.5rem)] w-[calc(100%-1rem)] flex-col ${width} -translate-x-1/2 -translate-y-1/2 overflow-hidden rounded-xl bg-white shadow-2xl ring-1 ring-gray-100 outline-none data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:zoom-in-95 sm:w-[calc(100%-2rem)]`}
        >
          <div className="h-[3px] w-full bg-gradient-to-r from-[#3CCED7] to-[#A6E661]" />
          <div className="flex shrink-0 items-start justify-between gap-3 px-4 pt-4 sm:px-5">
            <div className="min-w-0">
              <Dialog.Title className="break-words text-[15px] font-semibold text-gray-900">
                {title}
              </Dialog.Title>
              {subtitle && (
                <Dialog.Description className="mt-0.5 break-words text-xs text-gray-500">
                  {subtitle}
                </Dialog.Description>
              )}
            </div>
            <Dialog.Close asChild>
              <button
                type="button"
                aria-label="Close"
                className="-mr-1 inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-gray-400 transition hover:bg-gray-100 hover:text-gray-700"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            </Dialog.Close>
          </div>
          <div className="min-h-0 overflow-y-auto px-4 pb-4 pt-4 sm:px-5 sm:pb-5">{children}</div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
