import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

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
