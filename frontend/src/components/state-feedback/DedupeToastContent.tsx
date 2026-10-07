import type { ToastTag } from '@/lib/notificationStore';

type DedupeToastContentProps = {
  message: string;
  count: number;
  type: ToastTag;
};

export function toastLiveAnnouncement(message: string, count: number): string {
  if (count <= 1) return message;
  return `${message}. Repeated ${count} times.`;
}

export default function DedupeToastContent({ message, count, type }: DedupeToastContentProps) {
  const announcement = toastLiveAnnouncement(message, count);

  return (
    <span
      className="inline-flex items-center gap-2"
      data-testid={`toast-${type}`}
      role="status"
      aria-live="polite"
    >
      <span aria-hidden="true">{message}</span>
      {count > 1 ? (
        <span
          data-testid="toast-count-badge"
          aria-hidden="true"
          className="inline-flex min-w-[1.5rem] items-center justify-center rounded-full bg-white/20 px-1.5 py-0.5 text-xs font-semibold tabular-nums"
        >
          ×{count}
        </span>
      ) : null}
      <span className="sr-only" data-testid="toast-live-announcement">
        {announcement}
      </span>
    </span>
  );
}
