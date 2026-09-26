'use client';

/**
 * The pop-up the custom-order edits open in, shaped like the order page's other
 * dialogs (refund, change fulfilment): a dimmed backdrop, a white card, a title.
 *
 * Unlike those, clicking the backdrop does not close it — each of these holds a
 * half-typed form, and losing it to a stray click is worse than one more press
 * of Discard. Escape closes it unless a save is in flight.
 */

import { useEffect, useId } from 'react';
import { cn } from '@/lib/utils';

export function Modal({
  title,
  hint,
  onClose,
  busy,
  wide,
  children,
  footer,
}: {
  title: string;
  hint?: string;
  onClose: () => void;
  /** A save is in flight: the close controls do nothing until it answers. */
  busy?: boolean;
  wide?: boolean;
  children: React.ReactNode;
  footer?: React.ReactNode;
}) {
  const titleId = useId();

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && !busy) onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose, busy]);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      role="dialog"
      aria-modal="true"
      aria-labelledby={titleId}
    >
      <div
        className={cn(
          'flex max-h-[calc(100dvh-2rem)] w-full flex-col border border-gray-200 bg-white',
          wide ? 'max-w-3xl' : 'max-w-xl',
        )}
      >
        <div className="flex items-start justify-between gap-3 border-b border-gray-100 px-5 pt-4 pb-3">
          <div className="min-w-0">
            <h2 id={titleId} className="font-display text-lg text-primary">
              {title}
            </h2>
            {hint && <p className="mt-0.5 text-xs font-body text-gray-500">{hint}</p>}
          </div>
          <button
            type="button"
            onClick={onClose}
            disabled={busy}
            aria-label="Close"
            className="inline-flex min-h-11 min-w-11 items-center justify-center text-gray-400 hover:text-gray-600 disabled:opacity-40 md:min-h-0 md:min-w-0"
          >
            <span className="material-icons text-[18px]">close</span>
          </button>
        </div>
        <div className="flex-1 overflow-y-auto px-5 py-4">{children}</div>
        {footer && <div className="border-t border-gray-100 px-5 py-3">{footer}</div>}
      </div>
    </div>
  );
}
