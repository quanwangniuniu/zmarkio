'use client';

import { useEffect, useRef } from 'react';
import { useToasterStore } from 'react-hot-toast';
import { useNotificationStore } from '@/lib/notificationStore';

type ToasterToast = {
  id: string;
  dismissed: boolean;
};

/** Ids still on screen. A dismissed toast is already leaving, so its count must reset. */
export function activeToastIds(toasts: readonly ToasterToast[]): Set<string> {
  return new Set(toasts.filter((toast) => !toast.dismissed).map((toast) => toast.id));
}

export function dedupeKeysToClear(
  seenIds: Iterable<string>,
  activeIds: ReadonlySet<string>,
): string[] {
  return [...seenIds].filter((id) => !activeIds.has(id));
}

/**
 * react-hot-toast has no onClose. Watch its store and drop a dedupe entry
 * once that toast id is dismissed or removed.
 */
export default function ToastDedupeCleaner() {
  const { toasts } = useToasterStore();
  const seenIds = useRef(new Set<string>());

  useEffect(() => {
    const queue = useNotificationStore.getState().toastQueue;
    const active = activeToastIds(toasts);

    for (const id of active) {
      if (queue[id]) seenIds.current.add(id);
    }

    for (const id of dedupeKeysToClear(seenIds.current, active)) {
      useNotificationStore.getState().clearToast(id);
      seenIds.current.delete(id);
    }
  }, [toasts]);

  return null;
}
