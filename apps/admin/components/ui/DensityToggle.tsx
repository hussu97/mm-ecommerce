'use client';

import { useDensity } from '@/lib/density-context';
import { cn } from '@/lib/utils';

/**
 * Flip the console between comfortable and compact row density. Lives in the
 * shell header so it governs every table at once. One button that shows the
 * density you'd switch *to*, rather than a segmented pair — it is a preference,
 * not a primary action, and does not earn two slots in a 56px bar.
 */
export function DensityToggle({ className }: { className?: string }) {
  const { density, toggleDensity } = useDensity();
  const goingCompact = density === 'comfortable';
  return (
    <button
      onClick={toggleDensity}
      title={goingCompact ? 'Switch to compact rows' : 'Switch to comfortable rows'}
      aria-label={goingCompact ? 'Switch to compact rows' : 'Switch to comfortable rows'}
      className={cn(
        'flex items-center justify-center text-gray-400 hover:text-primary transition-colors',
        'min-w-[var(--tap-min)] min-h-[var(--tap-min)] md:min-w-0 md:min-h-0',
        className,
      )}
    >
      <span className="material-icons text-[18px]">
        {goingCompact ? 'density_small' : 'density_medium'}
      </span>
    </button>
  );
}
