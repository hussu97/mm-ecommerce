import { ButtonHTMLAttributes, forwardRef } from 'react';
import { cn } from '@/lib/utils';
import { Spinner } from './Spinner';

type Variant = 'primary' | 'secondary' | 'ghost';
type Size = 'sm' | 'md' | 'lg';

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: Size;
  loading?: boolean;
  fullWidth?: boolean;
}

const variantClasses: Record<Variant, string> = {
  primary:   'bg-primary text-white hover:opacity-90 active:opacity-80',
  secondary: 'bg-secondary text-white hover:opacity-90 active:opacity-80',
  ghost:     'bg-transparent border border-primary text-primary hover:bg-primary/10 active:bg-primary/20',
};

const sizeClasses: Record<Size, string> = {
  sm: 'text-xs px-3 py-1.5 tracking-widest',
  md: 'text-xs px-5 py-2.5 tracking-widest',
  lg: 'text-sm px-7 py-3.5 tracking-widest',
};

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(({
  variant = 'primary',
  size = 'md',
  loading = false,
  fullWidth = false,
  className,
  disabled,
  children,
  ...props
}, ref) => (
  <button
    ref={ref}
    disabled={disabled || loading}
    className={cn(
      'inline-flex items-center justify-center gap-2 font-body font-medium uppercase transition-all duration-200 cursor-pointer',
      'disabled:opacity-50 disabled:cursor-not-allowed',
      variantClasses[variant],
      sizeClasses[size],
      fullWidth && 'w-full',
      className,
    )}
    {...props}
  >
    {loading && <Spinner size="sm" className={variant === 'ghost' ? 'text-primary' : 'text-white'} />}
    {children}
  </button>
));

Button.displayName = 'Button';
