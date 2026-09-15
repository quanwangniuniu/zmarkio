'use client';

import toast, { type ToastOptions } from 'react-hot-toast';
import DedupeToastContent from '@/components/state-feedback/DedupeToastContent';
import { useNotificationStore, type ToastTag } from '@/lib/notificationStore';

export type DedupedToastOptions = ToastOptions & {
  /**
   * Scopes dedupe to a specific action (e.g. "klaviyo.create").
   * Same message with different operations will not merge.
   */
  operation?: string;
};

type DedupeToastResult = {
  dedupeKey: string;
  count: number;
  toastId: string;
};

function showDedupedToast(
  type: ToastTag,
  message: string,
  options?: DedupedToastOptions,
): DedupeToastResult {
  const { operation, ...toastOptions } = options ?? {};
  const { dedupeKey, count } = useNotificationStore.getState().incrementToast({
    message,
    type,
    operation,
  });

  const toastId = dedupeKey;
  const displayMessage =
    useNotificationStore.getState().toastQueue[dedupeKey]?.message ?? message;

  const content = (
    <DedupeToastContent message={displayMessage} count={count} type={type} />
  );

  switch (type) {
    case 'success':
      toast.success(content, { ...toastOptions, id: toastId });
      break;
    case 'error':
      toast.error(content, { ...toastOptions, id: toastId });
      break;
    case 'loading':
      toast.loading(content, { ...toastOptions, id: toastId });
      break;
    case 'info':
    default:
      toast(content, { ...toastOptions, id: toastId });
      break;
  }

  return { dedupeKey, count, toastId };
}

export const toastDeduped = {
  error(message: string, options?: DedupedToastOptions): DedupeToastResult {
    return showDedupedToast('error', message, options);
  },
  success(message: string, options?: DedupedToastOptions): DedupeToastResult {
    return showDedupedToast('success', message, options);
  },
  loading(message: string, options?: DedupedToastOptions): DedupeToastResult {
    return showDedupedToast('loading', message, options);
  },
  info(message: string, options?: DedupedToastOptions): DedupeToastResult {
    return showDedupedToast('info', message, options);
  },
};
