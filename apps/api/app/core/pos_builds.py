"""
Minimum mm-pos builds for register features the server cannot enable by itself.

Terminals update over TestFlight at different times, so old and new builds run
side by side, even within one branch. A feature that needs new app UI is gated
here by the build number every terminal reports (`X-App-Build`, stored on
`Device.build_number`), and the console warns where a branch relies on it while
an older terminal is still in service.
"""

from __future__ import annotations

#: The first mm-pos build that shows the coupon chips (`CouponBar`). A branch
#: set to run a promotion as a *coupon* needs it: an older terminal cannot
#: select a coupon, so at that branch the promotion simply never applies on it.
#: (Auto mode is applied server-side and needs no particular build.)
COUPON_MIN_BUILD = 1057

#: The first mm-pos build allowed to run the counter local-first (price, take
#: the tender for, number and print a sale on the iPad and sync it afterwards).
#: Below it, `GET /pos/counter/bundle` says `counter_local_first: "off"` whatever
#: the branch flag is, and `POST /pos/counter/sales` answers 426 — a guard
#: against an unfinished TestFlight build syncing sales the server cannot trust.
#: A terminal that reports no build is treated as below it.
#: Nothing changes at a branch until its `counter_local_first` flag is moved
#: off `off` as well.
COUNTER_LOCAL_FIRST_MIN_BUILD = 1057


def build_at_least(build_number: str | None, minimum: int) -> bool:
    """Whether a reported build number is at least `minimum`.

    An unknown or non-numeric build (a terminal predating the `X-App-*`
    headers) is treated as too old — the safe reading for a feature gate.
    """
    if build_number is None:
        return False
    try:
        return int(str(build_number).strip()) >= minimum
    except ValueError:
        return False
