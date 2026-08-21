import { fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { InfoTip } from './InfoTip';

const LABEL = 'What is this?';
const BODY = 'Orders of 35 AED or less carry a 15 AED fee.';

function renderTip() {
  render(<InfoTip label={LABEL}>{BODY}</InfoTip>);
  return screen.getByRole('button', { name: LABEL });
}

describe('InfoTip', () => {
  it('stays shut until asked', () => {
    const trigger = renderTip();
    expect(screen.queryByRole('tooltip')).toBeNull();
    expect(trigger).toHaveAttribute('aria-expanded', 'false');
  });

  it('opens on a tap or click, and describes the trigger while open', () => {
    const trigger = renderTip();

    fireEvent.click(trigger);

    const panel = screen.getByRole('tooltip');
    expect(panel).toHaveTextContent(BODY);
    expect(trigger).toHaveAttribute('aria-expanded', 'true');
    expect(trigger).toHaveAttribute('aria-describedby', panel.id);
  });

  it('opens on hover for a mouse, and closes when the mouse leaves', () => {
    const trigger = renderTip();
    const wrapper = trigger.parentElement!;

    fireEvent.pointerEnter(wrapper, { pointerType: 'mouse' });
    expect(screen.getByRole('tooltip')).toBeInTheDocument();

    fireEvent.pointerLeave(wrapper, { pointerType: 'mouse' });
    expect(screen.queryByRole('tooltip')).toBeNull();
  });

  it('ignores hover from a finger, which has no hover to leave with', () => {
    const trigger = renderTip();

    fireEvent.pointerEnter(trigger.parentElement!, { pointerType: 'touch' });

    expect(screen.queryByRole('tooltip')).toBeNull();
  });

  it('is dismissible with Escape without moving the pointer', () => {
    const trigger = renderTip();
    fireEvent.click(trigger);
    expect(screen.getByRole('tooltip')).toBeInTheDocument();

    fireEvent.keyDown(document, { key: 'Escape' });

    expect(screen.queryByRole('tooltip')).toBeNull();
    expect(trigger).toHaveAttribute('aria-expanded', 'false');
    // Focus deliberately stays put. Restoring it to the trigger would re-match
    // `:focus-visible` and reopen what Escape just dismissed.
  });

  it('is dismissible by pressing anywhere else', () => {
    const trigger = renderTip();
    fireEvent.click(trigger);

    fireEvent.pointerDown(document.body);

    expect(screen.queryByRole('tooltip')).toBeNull();
  });

  it('closes again on a second press', () => {
    const trigger = renderTip();

    fireEvent.click(trigger);
    fireEvent.click(trigger);

    expect(screen.queryByRole('tooltip')).toBeNull();
  });
});

describe('staying inside the viewport', () => {
  /**
   * The small-order-fee tip sits about a third of the way across a phone
   * screen, and the panel hangs back from its end edge. At 16rem that put its
   * left edge ~140px off the side of a 390px viewport and cut the first two
   * words off every line. `max-width` did not save it: the panel was not too
   * wide, it was in the wrong place.
   */
  function openWithBox(box: Partial<DOMRect>) {
    const rect = { left: 0, right: 0, top: 0, bottom: 0, width: 256, height: 80, x: 0, y: 0, toJSON: () => ({}) };
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue({ ...rect, ...box } as DOMRect);
    render(<InfoTip label="What is this?">Because the basket is small.</InfoTip>);
    fireEvent.click(screen.getByRole('button', { name: 'What is this?' }));
    return screen.getByRole('tooltip');
  }

  afterEach(() => vi.restoreAllMocks());

  it('pushes a panel that starts off the left edge back on screen', () => {
    window.innerWidth = 390;
    const panel = openWithBox({ left: -143, right: 113 });
    // 12px gutter, so it moves right by 143 + 12.
    expect(panel.style.transform).toBe('translateX(155px)');
  });

  it('pulls a panel that runs off the right edge back on screen', () => {
    window.innerWidth = 390;
    const panel = openWithBox({ left: 200, right: 456 });
    expect(panel.style.transform).toBe('translateX(-78px)');
  });

  it('leaves a panel that already fits exactly where it is', () => {
    window.innerWidth = 1280;
    const panel = openWithBox({ left: 900, right: 1156 });
    expect(panel.style.transform).toBe('');
  });
});
