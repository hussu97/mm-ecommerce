import { HTMLAttributes } from 'react';
import { cn } from '@/lib/utils';

interface CardProps extends HTMLAttributes<HTMLDivElement> {
  hover?: boolean;
  padding?: 'none' | 'sm' | 'md' | 'lg';
}

const paddingClasses = {
  none: '',
  sm:   'p-3',
  md:   'p-5',
  lg:   'p-7',
};

export function Card({ hover = false, padding = 'md', className, children, ...props }: CardProps) {
  return (
    <div
      className={cn(
        'bg-white border border-gray-100 rounded-sm',
        hover && 'transition-shadow duration-200 hover:shadow-md cursor-pointer',
        paddingClasses[padding],
        className,
      )}
      {...props}
    >
      {children}
    </div>
  );
}
