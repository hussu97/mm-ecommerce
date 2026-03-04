'use client';

import { cn } from '@/lib/utils';

interface QuantitySelectorProps {
  value: number;
  onChange: (value: number) => void;
  min?: number;
  max?: number;
  disabled?: boolean;
  className?: string;
}

export function QuantitySelector({
  value,
  onChange,
  min = 1,
  max = 99,
  disabled = false,
  className,
}: QuantitySelectorProps) {
  const decrement = () => { if (value > min) onChange(value - 1); };
  const increment = () => { if (value < max) onChange(value + 1); };

  return (
    <div className={cn('inline-flex items-center border border-gray-300 rounded-sm', disabled && 'opacity-50', className)}>
      <button
        type="button"
        onClick={decrement}
        disabled={disabled || value <= min}
        className="w-8 h-8 flex items-center justify-center text-gray-500 hover:text-primary hover:bg-gray-50 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
        aria-label="Decrease quantity"
      >
        <span className="material-icons text-sm">remove</span>
      </button>
      <input
        type="number"
        value={value}
        min={min}
        max={max}
        disabled={disabled}
        onChange={e => {
          const n = parseInt(e.target.value, 10);
          if (!isNaN(n) && n >= min && n <= max) onChange(n);
        }}
        className="w-10 h-8 text-center text-sm font-body border-x border-gray-300 bg-white outline-none [appearance:textfield] [&::-webkit-outer-spin-button]:appearance-none [&::-webkit-inner-spin-button]:appearance-none"
        aria-label="Quantity"
      />
      <button
        type="button"
        onClick={increment}
        disabled={disabled || value >= max}
        className="w-8 h-8 flex items-center justify-center text-gray-500 hover:text-primary hover:bg-gray-50 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
        aria-label="Increase quantity"
      >
        <span className="material-icons text-sm">add</span>
      </button>
    </div>
  );
}
