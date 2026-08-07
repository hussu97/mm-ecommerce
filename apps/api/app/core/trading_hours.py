"""
When the shop is open, on its own clock.

One definition, shared. The dispatcher needs it to decide whether retrying a
failed booking is worth anything at 3am; the delivery promise needs it to know
that an order placed at 23:30 against a 23:00 close cannot be baked until
tomorrow. Two answers to "is the kitchen open" is how a customer gets told
"tomorrow" for something that will not be started until the day after.

Hours are `"HH:MM"` strings on the branch, and they are read in `Asia/Dubai`
because that is the clock the staff and the customer are both standing on.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from app.models.delivery_batch import DELIVERY_TIMEZONE

__all__ = [
    "TZ",
    "at_minute",
    "local",
    "minutes_of",
    "next_opening",
    "is_open",
    "is_after_close",
]

TZ = ZoneInfo(DELIVERY_TIMEZONE)


def local(moment: datetime) -> datetime:
    """The same instant, read on the shop's clock."""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(TZ)


def minutes_of(clock: str | None) -> int | None:
    """`"HH:MM"` as a minute of the day, or None if it is not that."""
    if not clock:
        return None
    try:
        hour, _, minute = clock.partition(":")
        total = int(hour) * 60 + int(minute)
    except ValueError:
        return None
    return total if 0 <= total <= 1440 else None


def at_minute(day: date, minute: int) -> datetime:
    """
    A minute-of-day on a local date, as a real instant.

    Minute 1440 is the midnight that closes the day — 00:00 the next morning —
    written that way rather than as 23:59 so a window ending at 24:00 lands on
    midnight exactly and not a minute early.
    """
    return datetime.combine(
        day + timedelta(days=minute // 1440),
        time(hour=(minute % 1440) // 60, minute=minute % 60),
        tzinfo=TZ,
    )


def is_open(moment: datetime, opens_at: str | None, closes_at: str | None) -> bool:
    """
    Whether the branch is trading at this instant.

    Half-open, the same reading a batch window uses, and the same tolerance for
    a day that runs past midnight: a kitchen open 09:00–02:00 is open at 01:00.

    Unparseable hours are treated as **always open**. A promise that is slightly
    too optimistic because a branch record has a typo in it beats a shop that
    silently stops quoting delivery.
    """
    opens, closes = minutes_of(opens_at), minutes_of(closes_at)
    if opens is None or closes is None:
        return True
    minute = local(moment).hour * 60 + local(moment).minute
    if closes <= opens:  # trades past midnight
        return minute >= opens or minute < closes
    return opens <= minute < closes


def is_after_close(
    moment: datetime, opens_at: str | None, closes_at: str | None
) -> bool:
    """
    Whether today's trading has already finished at this instant.

    Deliberately narrower than `not is_open`. A moment *before* opening is also
    not open, and the two want opposite answers: an order at 07:00 against a
    09:00–23:00 day is still for today, while one at 23:30 is not. Only the
    second should push a next-day promise out by a day.

    A day that runs past midnight has no evening "after close" at all — 23:30 on
    a 09:00–02:00 kitchen is still trading, and the close it eventually reaches
    belongs to tomorrow's date.
    """
    opens, closes = minutes_of(opens_at), minutes_of(closes_at)
    if closes is None:
        return False
    if opens is not None and closes <= opens:
        # Trades past midnight: after the close (02:00) but before the open
        # (09:00) is the gap, and that is the only "after close" there is.
        here = local(moment)
        minute = here.hour * 60 + here.minute
        return closes <= minute < opens
    here = local(moment)
    return here.hour * 60 + here.minute >= closes


def next_opening(moment: datetime, opens_at: str | None) -> datetime:
    """
    The next time the branch opens, at or after `moment`.

    Returns `moment` itself when the hours cannot be read, which keeps the
    always-open reading of `is_open` consistent — a bad branch record must not
    push every promise to an invented opening time.
    """
    opens = minutes_of(opens_at)
    if opens is None:
        return moment
    here = local(moment)
    candidate = at_minute(here.date(), opens)
    if candidate < here:
        candidate = at_minute(here.date() + timedelta(days=1), opens)
    return candidate
