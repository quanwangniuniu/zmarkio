import { forwardRef } from 'react';
import type { ButtonHTMLAttributes, ReactNode } from 'react';
import LoadingSpinner from '../ui/LoadingSpinner';

type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'danger';
type ButtonSize = 'sm' | 'md' | 'lg';

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: ButtonVariant;
  size?: ButtonSize;
  loading?: boolean;
  leftIcon?: ReactNode;
  rightIcon?: ReactNode;
};

const baseClasses =
  'inline-flex items-center justify-center gap-2 rounded-md border text-sm font-medium transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50';

const sizeClasses: Record<ButtonSize, string> = {
  sm: 'px-3 py-1.5 text-xs',
  md: 'px-4 py-2 text-sm',
  lg: 'px-5 py-2.5 text-base',
};

const variantClasses: Record<ButtonVariant, string> = {
  primary:
    'border-transparent bg-primary text-primary-foreground hover:bg-primary/90 focus-visible:ring-ring',
  secondary:
    'border-slate-300 bg-white text-slate-700 hover:bg-slate-50 focus-visible:ring-slate-400',
  ghost:
    'border-transparent bg-transparent text-slate-700 hover:bg-slate-100 focus-visible:ring-slate-400',
  danger:
    'border-transparent bg-red-600 text-white hover:bg-red-700 focus-visible:ring-red-500',
};

const spinnerSizes: Record<ButtonSize, string> = {
  sm: 'h-3.5 w-3.5',
  md: 'h-4 w-4',
  lg: 'h-5 w-5',
};

const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  (
    {
      variant = 'primary',
      size = 'md',
      loading = false,
      leftIcon,
      rightIcon,
      disabled,
      className = '',
      children,
      type = 'button',
      ...props
    },
    ref,
  ) => (
    <button
      ref={ref}
      type={type}
      className={`${baseClasses} ${sizeClasses[size]} ${variantClasses[variant]} ${className}`}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      {...props}
    >
      {loading ? <LoadingSpinner className={spinnerSizes[size]} /> : leftIcon}
      <span>{children}</span>
      {rightIcon}
    </button>
  ),
);

Button.displayName = 'Button';

export default Button;
