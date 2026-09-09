'use client';

import toast, { type ToastOptions } from 'react-hot-toast';
import DedupeToastContent from '@/components/state-feedback/DedupeToastContent';
import { useNotificationStore, type ToastTag } from '@/lib/notificationStore';

type DedupeToastResult = {
  dedupeKey: string;
  count: number;
  toastId: string;
};

function showDedupedToast(type: ToastTag, message: string, options?: ToastOptions): DedupeToastResult {
  const { dedupeKey, count } = useNotificationStore.getState().incrementToast({
    message,
    type,
  });

  const toastId = dedupeKey;
  const displayMessage =
    useNotificationStore.getState().toastQueue[dedupeKey]?.message ?? message;

  const content = (
    <DedupeToastContent message={displayMessage} count={count} type={type} />
  );

  switch (type) {
    case 'success':
      toast.success(content, { ...options, id: toastId });
      break;
    case 'error':
      toast.error(content, { ...options, id: toastId });
      break;
    case 'loading':
      toast.loading(content, { ...options, id: toastId });
      break;
    case 'info':
    default:
      toast(content, { ...options, id: toastId });
      break;
  }

  return { dedupeKey, count, toastId };
}

export const toastDeduped = {
  error(message: string, options?: ToastOptions): DedupeToastResult {
    return showDedupedToast('error', message, options);
  },
  success(message: string, options?: ToastOptions): DedupeToastResult {
    return showDedupedToast('success', message, options);
  },
  loading(message: string, options?: ToastOptions): DedupeToastResult {
    return showDedupedToast('loading', message, options);
  },
  info(message: string, options?: ToastOptions): DedupeToastResult {
    return showDedupedToast('info', message, options);
  },
};
