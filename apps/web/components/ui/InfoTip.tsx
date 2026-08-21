'use client';

import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState } from 'react';
import { cn } from '@/lib/utils';
import { Icon } from '@/components/ui/Icon';

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
  const panelRef = useRef<HTMLSpanElement>(null);
  const [pinned, setPinned] = useState(false);
  const [hovered, setHovered] = useState(false);
  const open = pinned || hovered;

  /**
   * Nudge the panel back inside the viewport.
   *
   * The panel hangs off the trigger's end edge and is 16rem wide, which is fine
   * on a desktop summary and wrong on a phone: the small-order-fee tip sits
   * about a third of the way across a 390px screen, so 256px of panel extending
   * back from it started 140px off the left edge and the first two words of
   * every line were cut off. `max-w-[calc(100vw-2rem)]` did not help — it caps
   * the width, and the panel was not too wide, it was in the wrong place.
   *
   * Measured rather than guessed, because where the trigger lands depends on
   * the label beside it, the language (`end-0` flips under RTL) and the width of
   * the screen. `translateX` is in physical pixels, so one calculation covers
   * both directions.
   *
   * `useLayoutEffect` so the correction lands in the same paint as the panel; a
   * `useEffect` here shows one frame of the clipped position first, which reads
   * as a flinch. Written straight onto the node rather than held in state,
   * because that is what this is — a measurement pushed back into the DOM — and
   * routing it through `setState` inside a layout effect buys a second render
   * per open for a value no other code reads. The panel unmounts when it
   * closes, so every open starts from a clean node.
   */
  useLayoutEffect(() => {
    if (!open) return;
    const reposition = () => {
      const el = panelRef.current;
      if (!el) return;
      // Cleared before measuring, so the box read is always the panel's natural
      // position rather than one that already carries a correction — otherwise
      // a resize would shift an already-shifted panel.
      el.style.transform = '';
      const box = el.getBoundingClientRect();
      const GUTTER = 12;
      const dx =
        box.left < GUTTER
          ? GUTTER - box.left
          : box.right > window.innerWidth - GUTTER
            ? window.innerWidth - GUTTER - box.right
            : 0;
      if (dx) el.style.transform = `translateX(${dx}px)`;
    };
    reposition();
    // A phone rotating with the tip open is rare and cheap to handle; leaving it
    // out means the panel stays measured against a screen that no longer exists.
    window.addEventListener('resize', reposition);
    return () => window.removeEventListener('resize', reposition);
  }, [open]);

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
        <Icon name="info_outline" className="text-[15px] leading-none" />
      </button>

      {open && (
        <span
          ref={panelRef}
          id={panelId}
          role="tooltip"
          className="absolute top-full end-0 z-30 mt-1.5 w-64 max-w-[calc(100vw-1.5rem)] rounded-sm border border-gray-200 bg-white px-3 py-2.5 text-start font-body text-xs font-normal leading-relaxed text-gray-600 shadow-lg"
        >
          {children}
        </span>
      )}
    </span>
  );
}
