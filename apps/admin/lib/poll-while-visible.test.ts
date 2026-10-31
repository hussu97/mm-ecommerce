import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { pollWhileVisible } from './poll-while-visible';

let hidden = false;

function setHidden(value: boolean) {
  hidden = value;
  document.dispatchEvent(new Event('visibilitychange'));
}

describe('pollWhileVisible', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    hidden = false;
    vi.spyOn(document, 'hidden', 'get').mockImplementation(() => hidden);
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it('ticks on the interval while visible, without an immediate first tick', () => {
    const tick = vi.fn();
    const stop = pollWhileVisible(tick, 60_000);
    expect(tick).not.toHaveBeenCalled();
    vi.advanceTimersByTime(60_000);
    expect(tick).toHaveBeenCalledTimes(1);
    vi.advanceTimersByTime(120_000);
    expect(tick).toHaveBeenCalledTimes(3);
    stop();
  });

  it('stops while hidden and refreshes at once when visible again', () => {
    const tick = vi.fn();
    const stop = pollWhileVisible(tick, 60_000);
    setHidden(true);
    vi.advanceTimersByTime(10 * 60_000);
    expect(tick).not.toHaveBeenCalled();

    setHidden(false);
    expect(tick).toHaveBeenCalledTimes(1);
    // The interval restarts from the return, not from the original schedule.
    vi.advanceTimersByTime(59_999);
    expect(tick).toHaveBeenCalledTimes(1);
    vi.advanceTimersByTime(1);
    expect(tick).toHaveBeenCalledTimes(2);
    stop();
  });

  it('does not start while the tab is already hidden', () => {
    hidden = true;
    const tick = vi.fn();
    const stop = pollWhileVisible(tick, 60_000);
    vi.advanceTimersByTime(5 * 60_000);
    expect(tick).not.toHaveBeenCalled();
    setHidden(false);
    expect(tick).toHaveBeenCalledTimes(1);
    stop();
  });

  it('a repeated visible event does not double the timer', () => {
    const tick = vi.fn();
    const stop = pollWhileVisible(tick, 60_000);
    setHidden(false);
    setHidden(false);
    expect(tick).not.toHaveBeenCalled();
    vi.advanceTimersByTime(60_000);
    expect(tick).toHaveBeenCalledTimes(1);
    stop();
  });

  it('cleanup stops the timer and the listener', () => {
    const tick = vi.fn();
    const stop = pollWhileVisible(tick, 60_000);
    stop();
    vi.advanceTimersByTime(5 * 60_000);
    setHidden(true);
    setHidden(false);
    expect(tick).not.toHaveBeenCalled();
  });
});
