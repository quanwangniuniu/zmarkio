import type { ToastTag } from '@/lib/notificationStore';

type DedupeToastContentProps = {
  message: string;
  count: number;
  type: ToastTag;
};

export default function DedupeToastContent({ message, count, type }: DedupeToastContentProps) {
  return (
    <span className="inline-flex items-center gap-2" data-testid={`toast-${type}`}>
      <span>{message}</span>
      {count > 1 ? (
        <span
          data-testid="toast-count-badge"
          aria-label={`Repeated ${count} times`}
          className="inline-flex min-w-[1.5rem] items-center justify-center rounded-full bg-white/20 px-1.5 py-0.5 text-xs font-semibold tabular-nums"
        >
          ×{count}
        </span>
      ) : null}
    </span>
  );
}
