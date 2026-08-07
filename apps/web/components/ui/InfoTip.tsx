'use client';

import { useCallback, useEffect, useId, useRef, useState } from 'react';
import { cn } from '@/lib/utils';

/**
 * Whether this element was focused by the keyboard rather than by a press.
 *
 * Guarded because `matches` throws a `SyntaxError` on a selector the engine
 * does not know, and `:focus-visible` is exactly the kind of selector an older
 * browser — or a test DOM — has never heard of. A tip that does not open on Tab
 * is a small loss; one that takes the summary down with it is not.
 */
function isKeyboardFocus(el: Element): boolean {
  try {
    return el.matches(':focus-visible');
  } catch {
    return false;
  }
}

interface InfoTipProps {
  /**
   * The trigger's accessible name — what a screen reader announces and what a
   * mouse user sees in the native tooltip. Something that names the question
   * being answered ("What is this?"), never "info".
   */
  label: string;
  /** The explanation itself. Plain text; the panel styles it. */
  children: React.ReactNode;
  className?: string;
}

/**
 * A small "why am I being charged this?" explainer, hung off the line it
 * explains.
 *
 * Two ways in, because there are two kinds of pointer and only one of them can
 * hover: a mouse opens it by hovering, and a tap — or a click, or Enter on the
 * keyboard — pins it open until it is dismissed. Both states are tracked
 * separately rather than as one boolean, which is what stops the usual bug
 * where a mouse user clicks a tip that hover already opened and it blinks shut
 * underneath the cursor.
 *
 * Dismissible three ways: Escape, a press anywhere outside, or moving focus
 * away. That is not a nicety — WCAG 1.4.13 requires content revealed on hover
 * to be dismissible without moving the pointer, and requires it to stay open
 * while the pointer is over the content itself, which is why the hover handlers
 * sit on the wrapper and not on the button.
 *
 * Keyboard focus opens it; a click does not, so that the click handler is free
 * to mean "pin" rather than fighting the focus that precedes it. `:focus-visible`
 * is what tells the two apart.
 */
export function InfoTip({ label, children, className }: InfoTipProps) {
  const panelId = useId();
  const wrapperRef = useRef<HTMLSpanElement>(null);
  const [pinned, setPinned] = useState(false);
  const [hovered, setHovered] = useState(false);
  const open = pinned || hovered;

  const dismiss = useCallback(() => {
    setPinned(false);
    setHovered(false);
  }, []);

  // Escape from anywhere, and a press anywhere else on the page. Registered
  // only while something is open, so a page full of these costs nothing to
  // render.
  useEffect(() => {
    if (!open) return;

    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') dismiss();
      // Deliberately without moving focus. A keyboard user is already on the
      // trigger, so there is nothing to restore — and refocusing it would
      // re-match `:focus-visible` and reopen the panel the Escape just closed.
      // A mouse user is somewhere else entirely, and dragging their focus onto
      // an icon they never selected is not a dismissal, it is a jump.
    };
    const onPointerDown = (e: PointerEvent) => {
      if (!wrapperRef.current?.contains(e.target as Node)) dismiss();
    };

    document.addEventListener('keydown', onKeyDown);
    document.addEventListener('pointerdown', onPointerDown);
    return () => {
      document.removeEventListener('keydown', onKeyDown);
      document.removeEventListener('pointerdown', onPointerDown);
    };
  }, [open, dismiss]);

  return (
    <span
      ref={wrapperRef}
      className={cn('relative inline-flex align-middle', className)}
      // Only a mouse hovers. A touch "pointerenter" arrives with the tap that
      // is already going to pin it, and treating that as hover would leave the
      // panel open with nothing to close it.
      onPointerEnter={(e) => { if (e.pointerType === 'mouse') setHovered(true); }}
      onPointerLeave={(e) => { if (e.pointerType === 'mouse') setHovered(false); }}
    >
      <button
        type="button"
        aria-label={label}
        title={label}
        aria-expanded={open}
        aria-describedby={open ? panelId : undefined}
        onClick={() => setPinned((p) => !p)}
        onFocus={(e) => { if (isKeyboardFocus(e.target)) setPinned(true); }}
        onBlur={() => setPinned(false)}
        className="inline-flex items-center justify-center text-gray-400 hover:text-primary focus:outline-none focus-visible:ring-2 focus-visible:ring-primary/40 rounded-full transition-colors"
      >
        <span className="material-icons text-[15px] leading-none">info_outline</span>
      </button>

      {open && (
        <span
          id={panelId}
          role="tooltip"
          className="absolute top-full end-0 z-30 mt-1.5 w-64 max-w-[calc(100vw-2rem)] rounded-sm border border-gray-200 bg-white px-3 py-2.5 text-start font-body text-xs font-normal leading-relaxed text-gray-600 shadow-lg"
        >
          {children}
        </span>
      )}
    </span>
  );
}
