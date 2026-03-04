import { cn } from '@/lib/utils';

type Variant = 'primary' | 'secondary' | 'success' | 'warning' | 'error' | 'outline';

const variantClasses: Record<Variant, string> = {
  primary:   'bg-primary text-white',
  secondary: 'bg-secondary text-white',
  success:   'bg-green-100 text-green-800',
  warning:   'bg-yellow-100 text-yellow-800',
  error:     'bg-red-100 text-red-700',
  outline:   'border border-primary text-primary bg-transparent',
};

interface BadgeProps {
  variant?: Variant;
  children: React.ReactNode;
  className?: string;
}

export function Badge({ variant = 'primary', children, className }: BadgeProps) {
  return (
    <span className={cn(
      'inline-flex items-center px-2 py-0.5 text-xs font-medium font-body uppercase tracking-wider rounded-sm',
      variantClasses[variant],
      className,
    )}>
      {children}
    </span>
  );
}
