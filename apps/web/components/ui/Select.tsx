import { SelectHTMLAttributes, forwardRef } from 'react';
import { cn } from '@/lib/utils';

interface SelectOption {
  value: string;
  label: string;
}

interface SelectProps extends SelectHTMLAttributes<HTMLSelectElement> {
  label?: string;
  error?: string;
  options: SelectOption[];
  placeholder?: string;
}

export const Select = forwardRef<HTMLSelectElement, SelectProps>(({
  label,
  error,
  options,
  placeholder,
  className,
  id,
  ...props
}, ref) => {
  const selectId = id || (label ? label.toLowerCase().replace(/\s+/g, '-') : undefined);
  return (
    <div className="w-full">
      {label && (
        <label htmlFor={selectId} className="block text-xs font-medium uppercase tracking-wider text-gray-600 mb-1.5">
          {label}
        </label>
      )}
      <select
        ref={ref}
        id={selectId}
        className={cn(
          'w-full px-3.5 py-2.5 text-sm font-body bg-white border rounded-sm outline-none appearance-none transition-all duration-200 cursor-pointer',
          'focus:border-primary focus:ring-2 focus:ring-primary/20',
          error ? 'border-red-400' : 'border-gray-300',
          'disabled:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-60',
          className,
        )}
        {...props}
      >
        {placeholder && <option value="">{placeholder}</option>}
        {options.map(({ value, label }) => (
          <option key={value} value={value}>{label}</option>
        ))}
      </select>
      {error && <p className="mt-1.5 text-xs text-red-500">{error}</p>}
    </div>
  );
});

Select.displayName = 'Select';
