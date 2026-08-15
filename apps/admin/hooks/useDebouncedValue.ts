import { useEffect, useState } from 'react';

/**
 * The value, once it has stopped moving for `ms`.
 *
 * For search boxes that drive a request: feed the raw input into this and the
 * debounced result into the fetch, and the API sees one call per pause rather
 * than one per keystroke. This used to live as a copy-pasted
 * `debounceRef`/`setTimeout` block on the orders and customers pages — and
 * not at all on the products page, which fired a request per keystroke.
 */
export function useDebouncedValue<T>(value: T, ms = 350): T {
  const [debounced, setDebounced] = useState(value);

  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), ms);
    return () => clearTimeout(timer);
  }, [value, ms]);

  return debounced;
}
