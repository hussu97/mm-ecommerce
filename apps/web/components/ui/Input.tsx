import { InputHTMLAttributes, forwardRef } from 'react';
import { cn } from '@/lib/utils';

interface InputProps extends InputHTMLAttributes<HTMLInputElement> {
  label?: string;
  error?: string;
  helper?: string;
}

export const Input = forwardRef<HTMLInputElement, InputProps>(({
  label,
  error,
  helper,
  className,
  id,
  ...props
}, ref) => {
  const inputId = id || (label ? label.toLowerCase().replace(/\s+/g, '-') : undefined);
  return (
    <div className="w-full">
      {label && (
        <label htmlFor={inputId} className="block text-xs font-medium uppercase tracking-wider text-gray-600 mb-1.5">
          {label}
        </label>
      )}
      <input
        ref={ref}
        id={inputId}
        className={cn(
          'w-full px-3.5 py-2.5 text-sm font-body bg-white border rounded-sm outline-none transition-all duration-200',
          'placeholder:text-gray-400',
          'focus:border-primary focus:ring-2 focus:ring-primary/20',
          error
            ? 'border-red-400 focus:border-red-400 focus:ring-red-400/20'
            : 'border-gray-300',
          'disabled:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-60',
          className,
        )}
        {...props}
      />
      {error && <p className="mt-1.5 text-xs text-red-500">{error}</p>}
      {helper && !error && <p className="mt-1.5 text-xs text-gray-500">{helper}</p>}
    </div>
  );
});

Input.displayName = 'Input';
