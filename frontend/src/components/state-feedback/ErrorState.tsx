import type { ReactNode } from 'react';

type ErrorStateProps = {
  title: string;
  description?: string;
  actionLabel?: string;
  onAction?: () => void;
  icon?: ReactNode;
  className?: string;
};

export default function ErrorState({
  title,
  description,
  actionLabel,
  onAction,
  icon,
  className = '',
}: ErrorStateProps) {
  return (
    <div
      role="alert"
      className={`flex w-full flex-col items-start gap-3 rounded-2xl border border-red-200 bg-red-50 p-5 text-red-900 ${className}`}
    >
      <div className="flex items-center gap-2">
        <span className="text-xl" aria-hidden="true">
          {icon ?? '⚠'}
        </span>
        <span className="text-sm font-semibold uppercase tracking-wide text-red-700">Error</span>
      </div>
      <div className="text-lg font-semibold text-red-900">{title}</div>
      {description ? <p className="text-sm text-red-700">{description}</p> : null}
      {actionLabel ? (
        <button
          type="button"
          onClick={onAction}
          className="rounded-full border border-red-200 bg-white px-4 py-2 text-sm font-semibold text-red-700 transition hover:border-red-300 hover:bg-red-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-red-300"
        >
          {actionLabel}
        </button>
      ) : null}
    </div>
  );
}
