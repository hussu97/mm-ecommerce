import { cn } from '@/lib/utils';
import { InputHTMLAttributes, SelectHTMLAttributes, TextareaHTMLAttributes, forwardRef, useEffect, useRef, useState } from 'react';

// ─── Button ───────────────────────────────────────────────────────────────────

type BtnVariant = 'primary' | 'secondary' | 'ghost' | 'outline' | 'danger';
type BtnSize = 'sm' | 'md';

interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: BtnVariant;
  size?: BtnSize;
  loading?: boolean;
}

const BtnVariants: Record<BtnVariant, string> = {
  primary:   'bg-primary text-white hover:opacity-90',
  secondary: 'bg-gray-100 text-gray-700 hover:bg-gray-200',
  ghost:     'border border-gray-300 text-gray-600 hover:bg-gray-50',
  outline:   'border border-gray-300 text-gray-600 hover:bg-gray-50',
  danger:    'bg-red-50 text-red-600 border border-red-200 hover:bg-red-100',
};
const BtnSizes: Record<BtnSize, string> = {
  sm: 'text-xs px-3 py-1.5',
  md: 'text-xs px-4 py-2',
};

export function Button({ variant = 'primary', size = 'md', loading, className, disabled, children, ...props }: ButtonProps) {
  return (
    <button
      disabled={disabled || loading}
      className={cn(
        'inline-flex items-center justify-center gap-1.5 font-body font-medium uppercase tracking-wider transition-all duration-150 cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed',
        BtnVariants[variant],
        BtnSizes[size],
        className,
      )}
      {...props}
    >
      {loading && (
        <span className="w-3.5 h-3.5 border-2 border-current border-t-transparent rounded-full animate-spin" />
      )}
      {children}
    </button>
  );
}

// ─── Input ────────────────────────────────────────────────────────────────────

interface InputProps extends InputHTMLAttributes<HTMLInputElement> {
  label?: string;
  error?: string;
  helper?: string;
}

export const Input = forwardRef<HTMLInputElement, InputProps>(({ label, error, helper, className, id, ...props }, ref) => {
  const inputId = id || (label ? label.toLowerCase().replace(/\s+/g, '-') : undefined);
  return (
    <div className="w-full">
      {label && <label htmlFor={inputId} className="block text-xs font-medium uppercase tracking-wider text-gray-600 mb-1">{label}</label>}
      <input
        ref={ref}
        id={inputId}
        className={cn(
          'w-full px-3 py-2 text-sm font-body bg-white border rounded-sm outline-none transition-colors',
          'placeholder:text-gray-400 focus:border-primary focus:ring-1 focus:ring-primary/30',
          error ? 'border-red-400' : 'border-gray-300',
          'disabled:bg-gray-50 disabled:opacity-60',
          className,
        )}
        {...props}
      />
      {error && <p className="mt-1 text-xs text-red-500">{error}</p>}
      {helper && !error && <p className="mt-1 text-xs text-gray-400">{helper}</p>}
    </div>
  );
});
Input.displayName = 'Input';

// ─── Select ───────────────────────────────────────────────────────────────────

interface SelectProps extends SelectHTMLAttributes<HTMLSelectElement> {
  label?: string;
  error?: string;
  options: { value: string; label: string }[];
  placeholder?: string;
}

export const Select = forwardRef<HTMLSelectElement, SelectProps>(({ label, error, options, placeholder, className, id, ...props }, ref) => {
  const selectId = id || (label ? label.toLowerCase().replace(/\s+/g, '-') : undefined);
  return (
    <div className="w-full">
      {label && <label htmlFor={selectId} className="block text-xs font-medium uppercase tracking-wider text-gray-600 mb-1">{label}</label>}
      <select
        ref={ref}
        id={selectId}
        className={cn(
          'w-full px-3 py-2 text-sm font-body bg-white border rounded-sm outline-none transition-colors cursor-pointer',
          'focus:border-primary focus:ring-1 focus:ring-primary/30',
          error ? 'border-red-400' : 'border-gray-300',
          className,
        )}
        {...props}
      >
        {placeholder && <option value="">{placeholder}</option>}
        {options.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
      </select>
      {error && <p className="mt-1 text-xs text-red-500">{error}</p>}
    </div>
  );
});
Select.displayName = 'Select';

// ─── Textarea ─────────────────────────────────────────────────────────────────

interface TextareaProps extends TextareaHTMLAttributes<HTMLTextAreaElement> {
  label?: string;
  error?: string;
}

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaProps>(({ label, error, className, id, ...props }, ref) => {
  const textareaId = id || (label ? label.toLowerCase().replace(/\s+/g, '-') : undefined);
  return (
    <div className="w-full">
      {label && <label htmlFor={textareaId} className="block text-xs font-medium uppercase tracking-wider text-gray-600 mb-1">{label}</label>}
      <textarea
        ref={ref}
        id={textareaId}
        className={cn(
          'w-full px-3 py-2 text-sm font-body bg-white border rounded-sm outline-none transition-colors resize-none',
          'placeholder:text-gray-400 focus:border-primary focus:ring-1 focus:ring-primary/30',
          error ? 'border-red-400' : 'border-gray-300',
          className,
        )}
        {...props}
      />
      {error && <p className="mt-1 text-xs text-red-500">{error}</p>}
    </div>
  );
});
Textarea.displayName = 'Textarea';

// ─── Badge ────────────────────────────────────────────────────────────────────

type BadgeVariant = 'success' | 'warning' | 'danger' | 'info' | 'neutral';

const BadgeVariants: Record<BadgeVariant, string> = {
  success: 'bg-green-50 text-green-700 border-green-200',
  warning: 'bg-yellow-50 text-yellow-700 border-yellow-200',
  danger:  'bg-red-50 text-red-600 border-red-200',
  info:    'bg-blue-50 text-blue-700 border-blue-200',
  neutral: 'bg-gray-100 text-gray-600 border-gray-200',
};

export function Badge({ variant = 'neutral', children, className }: { variant?: BadgeVariant; children: React.ReactNode; className?: string }) {
  return (
    <span className={cn('inline-block text-[11px] font-body px-2 py-0.5 border uppercase tracking-wide', BadgeVariants[variant], className)}>
      {children}
    </span>
  );
}

// ─── Spinner ──────────────────────────────────────────────────────────────────

export function Spinner({ className }: { className?: string }) {
  return (
    <div className={cn('w-6 h-6 border-2 border-primary border-t-transparent rounded-full animate-spin', className)} />
  );
}

// ─── MultiSelect ──────────────────────────────────────────────────────────────

interface MultiSelectOption { value: string; label: string }
interface MultiSelectProps {
  options: MultiSelectOption[];
  value: string[];
  onChange: (value: string[]) => void;
  placeholder?: string;
  className?: string;
}

export function MultiSelect({ options, value, onChange, placeholder = 'All', className }: MultiSelectProps) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    function handleClickOutside(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, [open]);

  const label = value.length === 0
    ? placeholder
    : value.length <= 2
    ? value.map(v => options.find(o => o.value === v)?.label ?? v).join(', ')
    : `${value.length} selected`;

  function toggle(val: string) {
    onChange(value.includes(val) ? value.filter(v => v !== val) : [...value, val]);
  }

  return (
    <div ref={ref} className={cn('relative', className)}>
      <button
        type="button"
        onClick={() => setOpen(o => !o)}
        className={cn(
          'w-full px-3 py-2 text-sm font-body bg-white border rounded-sm outline-none transition-colors flex items-center justify-between gap-2 cursor-pointer',
          open ? 'border-primary ring-1 ring-primary/30' : 'border-gray-300',
          value.length > 0 ? 'text-gray-700' : 'text-gray-400',
        )}
      >
        <span className="truncate">{label}</span>
        <span className="material-icons text-[14px] text-gray-400 shrink-0">expand_more</span>
      </button>
      {open && (
        <div className="absolute z-20 top-full left-0 right-0 bg-white border border-gray-200 shadow-md mt-0.5 max-h-52 overflow-y-auto">
          {value.length > 0 && (
            <button
              type="button"
              onClick={() => { onChange([]); setOpen(false); }}
              className="w-full px-3 py-1.5 text-left text-xs font-body text-primary hover:bg-primary/5 border-b border-gray-100"
            >
              Clear selection
            </button>
          )}
          {options.map(opt => (
            <label key={opt.value} className="flex items-center gap-2.5 px-3 py-2 hover:bg-gray-50 cursor-pointer">
              <input
                type="checkbox"
                checked={value.includes(opt.value)}
                onChange={() => toggle(opt.value)}
                className="accent-primary shrink-0"
              />
              <span className="text-sm font-body text-gray-700">{opt.label}</span>
            </label>
          ))}
        </div>
      )}
    </div>
  );
}

// ─── Pagination ───────────────────────────────────────────────────────────────

const PER_PAGE_OPTIONS = [50, 100, 200, 500, 1000, 2000];

interface PaginationProps {
  page: number;
  pages: number;
  total: number;
  perPage: number;
  onPageChange: (page: number) => void;
  onPerPageChange: (perPage: number) => void;
  label?: string;
}

export function Pagination({ page, pages, total, perPage, onPageChange, onPerPageChange, label = 'items' }: PaginationProps) {
  const totalPages = Math.max(1, pages);
  return (
    <div className="flex items-center justify-between mt-4">
      <p className="text-xs text-gray-400 font-body">
        Page {page} of {totalPages} · {total} {label}
      </p>
      <div className="flex items-center gap-2">
        <select
          value={perPage}
          onChange={e => { onPerPageChange(Number(e.target.value)); onPageChange(1); }}
          className="text-xs font-body border border-gray-300 bg-white px-2 py-1.5 rounded-sm outline-none focus:border-primary cursor-pointer"
        >
          {PER_PAGE_OPTIONS.map(n => (
            <option key={n} value={n}>{n} / page</option>
          ))}
        </select>
        <Button variant="ghost" size="sm" disabled={page <= 1} onClick={() => onPageChange(page - 1)}>
          <span className="material-icons text-[14px]">chevron_left</span>
        </Button>
        <Button variant="ghost" size="sm" disabled={page >= totalPages} onClick={() => onPageChange(page + 1)}>
          <span className="material-icons text-[14px]">chevron_right</span>
        </Button>
      </div>
    </div>
  );
}

// ─── TabBar ───────────────────────────────────────────────────────────────────

interface TabBarProps {
  tabs: { key: string; label: string; count?: number }[];
  active: string;
  onChange: (key: string) => void;
}

export function TabBar({ tabs, active, onChange }: TabBarProps) {
  return (
    <div className="flex border-b border-gray-200 mb-4">
      {tabs.map(tab => (
        <button
          key={tab.key}
          onClick={() => onChange(tab.key)}
          className={cn(
            'px-4 py-2 text-xs font-body font-medium uppercase tracking-wider transition-colors border-b-2 -mb-px',
            active === tab.key
              ? 'text-primary border-primary'
              : 'text-gray-500 border-transparent hover:text-gray-700',
          )}
        >
          {tab.label}
          {tab.count !== undefined && (
            <span className={cn(
              'ml-1.5 text-[10px] px-1.5 py-0.5 rounded-full',
              active === tab.key ? 'bg-primary/10 text-primary' : 'bg-gray-100 text-gray-500',
            )}>
              {tab.count}
            </span>
          )}
        </button>
      ))}
    </div>
  );
}
