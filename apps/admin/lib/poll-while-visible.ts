/**
 * Run `tick` every `intervalMs` — but only while the tab is visible.
 *
 * A console tab left open in the background used to keep polling (the dashboard
 * hit `/dashboard/today` every 60s all day for nobody), spending the API's one
 * event loop on figures no one was looking at. Hidden, the timer is cleared;
 * when the tab comes back, `tick` runs at once (so the figures are current the
 * moment someone looks) and the interval restarts from there.
 *
 * Does not run `tick` on start — the caller does its own first load. Returns
 * the cleanup for a `useEffect`.
 */
export function pollWhileVisible(tick: () => void, intervalMs: number): () => void {
  let timer: ReturnType<typeof setInterval> | null = null;

  const start = () => {
    if (timer === null) timer = setInterval(tick, intervalMs);
  };
  const stop = () => {
    if (timer !== null) {
      clearInterval(timer);
      timer = null;
    }
  };
  const onVisibilityChange = () => {
    if (document.hidden) {
      stop();
    } else if (timer === null) {
      tick();
      start();
    }
  };

  if (!document.hidden) start();
  document.addEventListener('visibilitychange', onVisibilityChange);

  return () => {
    stop();
    document.removeEventListener('visibilitychange', onVisibilityChange);
  };
}
