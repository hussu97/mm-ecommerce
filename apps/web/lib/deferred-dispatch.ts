/**
 * Send something to a third-party script that may not have loaded yet.
 *
 * Both trackers on this site have the same race. Their scripts are
 * `afterInteractive`, so anything a page reports on mount runs first, and the
 * global they call through does not exist yet. Umami lost events to it for real:
 * the queue used to be four fixed timers — 100ms, 500ms, 1.5s, 3s, all scheduled
 * from the first queued item — and after the last of them nothing ever looked
 * again. An event queued on a connection where a third-party script takes more
 * than three seconds was dropped for good, silently, with no retry and no error.
 * That window is smallest on exactly the page it matters most on, the order
 * confirmation, which arrives as a fresh document from the payment gateway and
 * reports `order_completed` as soon as its first API call returns.
 *
 * A plain poll to a generous ceiling costs a handful of property reads and
 * cannot fail that way. It stops the moment the script appears, and starts again
 * if something is queued after it has given up — a visitor who unblocks the
 * script, or a network that recovers, is not written off for the rest of the
 * session.
 */

const POLL_INTERVAL_MS = 250;
const POLL_CEILING_MS = 30_000;

/**
 * Ceiling on what is held while waiting.
 *
 * A session behind a content blocker never resolves the target, so without a
 * cap the queue grows for as long as the tab is open — and it now grows twice as
 * fast, because every event goes to two trackers. 500 is far past any real
 * session (the busiest measured page produces a few dozen) and small enough that
 * holding it costs nothing. The oldest go first: what a dropped event costs is
 * context for the ones around it, and the newest are the ones still being
 * looked at.
 */
const MAX_QUEUED = 500;

/**
 * A `send` bound to a target that arrives later.
 *
 * `resolve` is called on every attempt rather than captured once, because the
 * whole point is that it answers `undefined` until the script has loaded — and
 * for Clarity the global is *replaced* by the real implementation when the tag
 * arrives, so a captured reference would be the wrong function.
 */
export function createDeferredDispatch<TTarget, TItem>({
  resolve,
  send,
}: {
  resolve: () => TTarget | null | undefined;
  send: (target: TTarget, item: TItem) => void;
}): (item: TItem) => void {
  const queue: TItem[] = [];
  let pollTimer: number | null = null;

  function flush(): boolean {
    const target = resolve();
    if (!target) return false;

    // `shift` per iteration, not a copy-and-clear: `send` runs third-party code
    // that can queue something of its own, and that item belongs at the back of
    // this queue rather than in a batch that has already been snapshotted.
    while (queue.length > 0) {
      send(target, queue.shift()!);
    }
    return true;
  }

  function stopPolling() {
    if (pollTimer !== null) {
      window.clearInterval(pollTimer);
      pollTimer = null;
    }
  }

  function schedulePoll() {
    if (pollTimer !== null) return;

    const deadline = Date.now() + POLL_CEILING_MS;
    pollTimer = window.setInterval(() => {
      if (flush() || Date.now() > deadline) stopPolling();
    }, POLL_INTERVAL_MS);
  }

  return function dispatch(item: TItem) {
    if (typeof window === 'undefined') return;

    // Queue first, then drain — so an item produced while something older is
    // still waiting cannot overtake it.
    queue.push(item);
    if (queue.length > MAX_QUEUED) queue.shift();

    if (!flush()) schedulePoll();
  };
}
