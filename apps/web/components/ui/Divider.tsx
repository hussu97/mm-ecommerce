import { cn } from '@/lib/utils';

interface DividerProps {
  label?: string;
  className?: string;
  orientation?: 'horizontal' | 'vertical';
}

export function Divider({ label, className, orientation = 'horizontal' }: DividerProps) {
  if (orientation === 'vertical') {
    return <div className={cn('w-px self-stretch bg-gray-200', className)} />;
  }

  if (label) {
    return (
      <div className={cn('flex items-center gap-3 my-4', className)}>
        <div className="flex-1 h-px bg-gray-200" />
        <span className="text-xs text-gray-400 font-body uppercase tracking-widest">{label}</span>
        <div className="flex-1 h-px bg-gray-200" />
      </div>
    );
  }

  return <hr className={cn('border-none h-px bg-gray-200', className)} />;
}
