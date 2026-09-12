import { cn } from '@/lib/utils';

/**
 * The one place the console decides how wide a screen's content may grow.
 *
 * It used to be decided per page, and drifted: `max-w-[1400px]` on some, 1500,
 * 1600 or 1100 on others, and several also re-added a `p-6` gutter the shell
 * already provides — so a wide table was boxed into 1400px with the rest of a
 * 1920px monitor left as dead gutter, differently on every screen.
 *
 * Two intents now, and only two:
 *
 *   `full`     the default. No cap. A list or a table takes the whole content
 *              column, so widening the window adds columns-in-view, not gutter.
 *   `reading`  capped at `--content-max`. For prose and detail/forms, where a
 *              line stretched across an ultrawide monitor is harder to read, not
 *              easier.
 *
 * Never set page padding here or in the page: `app/(dashboard)/layout.tsx` owns
 * the single gutter (rule 1 of the mobile design system).
 */
export function Page({
  maxWidth = 'full',
  className,
  children,
}: {
  maxWidth?: 'full' | 'reading';
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <div className={cn(maxWidth === 'reading' && 'max-w-[var(--content-max)]', className)}>
      {children}
    </div>
  );
}
