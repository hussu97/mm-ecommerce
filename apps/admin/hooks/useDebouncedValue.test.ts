import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useDebouncedValue } from './useDebouncedValue';

describe('useDebouncedValue', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it('returns the initial value immediately', () => {
    const { result } = renderHook(() => useDebouncedValue('cake', 350));
    expect(result.current).toBe('cake');
  });

  it('holds the old value until the delay elapses', () => {
    const { result, rerender } = renderHook(({ value }) => useDebouncedValue(value, 350), {
      initialProps: { value: 'c' },
    });
    rerender({ value: 'cho' });
    act(() => vi.advanceTimersByTime(300));
    expect(result.current).toBe('c');
    act(() => vi.advanceTimersByTime(50));
    expect(result.current).toBe('cho');
  });

  it('restarts the timer on every change — only the final value lands', () => {
    const { result, rerender } = renderHook(({ value }) => useDebouncedValue(value, 350), {
      initialProps: { value: 'c' },
    });
    rerender({ value: 'ch' });
    act(() => vi.advanceTimersByTime(200));
    rerender({ value: 'chocolate' });
    act(() => vi.advanceTimersByTime(200));
    // 400ms after the first keystroke, but only 200ms after the last one.
    expect(result.current).toBe('c');
    act(() => vi.advanceTimersByTime(150));
    expect(result.current).toBe('chocolate');
  });
});
