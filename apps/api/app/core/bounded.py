"""A process-local mapping that cannot grow without bound (F-COU-21).

Several courier and zone quote caches key on rounded coordinates or a
zone-version id and stamp each value with its own insertion time, checking
freshness on read. Nothing ever removes a cold key, so on a long-lived worker
the dict gains one entry per distinct pin (or per superseded zone version)
forever — a slow leak that only a redeploy clears.

`BoundedLRU` is the smallest fix that keeps every call site unchanged: it is a
dict (via `OrderedDict`) with a hard entry cap that evicts the least-recently
used key when a new one would overflow it. It is deliberately NOT a TTL cache —
each caller already owns its freshness clock; this bounds size and nothing else.
"""

from __future__ import annotations

from collections import OrderedDict
from typing import TypeVar

K = TypeVar("K")
V = TypeVar("V")


class BoundedLRU(OrderedDict[K, V]):
    """An `OrderedDict` capped at `maxsize` entries, LRU-evicting on overflow.

    Reads via `get` and writes via `cache[key] = value` both mark the key as
    most-recently used; a write that pushes the size past `maxsize` drops the
    oldest key. `clear()` and iteration behave as on a normal dict.
    """

    def __init__(self, maxsize: int) -> None:
        if maxsize < 1:
            raise ValueError("maxsize must be >= 1")
        super().__init__()
        self._maxsize = maxsize

    @property
    def maxsize(self) -> int:
        return self._maxsize

    def __setitem__(self, key: K, value: V) -> None:
        super().__setitem__(key, value)
        self.move_to_end(key)
        while len(self) > self._maxsize:
            self.popitem(last=False)

    def get(self, key, default=None):  # type: ignore[override]
        if key in self:
            self.move_to_end(key)
            return self[key]
        return default
