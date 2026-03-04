import { cn } from '@/lib/utils';

type Size = 'sm' | 'md' | 'lg';

const sizeClasses: Record<Size, string> = {
  sm: 'w-4 h-4 border-[2px]',
  md: 'w-6 h-6 border-[2px]',
  lg: 'w-8 h-8 border-[3px]',
};

interface SpinnerProps {
  size?: Size;
  className?: string;
}

export function Spinner({ size = 'md', className }: SpinnerProps) {
  return (
    <span
      role="status"
      aria-label="Loading"
      className={cn(
        'inline-block rounded-full border-current border-t-transparent animate-spin',
        sizeClasses[size],
        className,
      )}
    />
  );
}
