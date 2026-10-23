"""The shop's local time for what a person reads: Europe/London (GMT, BST in summer). The database keeps UTC.

Written out here (not zoneinfo): this Windows has no time zone database, and requirements.txt is frozen by
`dxf-blind-freeze`. The UK rule since 1996 (EU rule): BST, UTC+1, from 01:00 UTC on the last Sunday of March to
01:00 UTC on the last Sunday of October. Later: a "shop time zone" setting (see CLAUDE.md).
"""
from datetime import datetime, timedelta, timezone

ZONE = "Europe/London"


def _last_sunday(year, month):
    day = datetime(year, month + 1, 1) - timedelta(days=1) if month < 12 else datetime(year, 12, 31)
    return day - timedelta(days=(day.weekday() + 1) % 7)


def is_bst(utc):
    """True when the moment (naive UTC, or aware) is in British Summer Time."""
    if utc.tzinfo is not None:
        utc = utc.astimezone(timezone.utc).replace(tzinfo=None)
    start = _last_sunday(utc.year, 3).replace(hour=1)
    end = _last_sunday(utc.year, 10).replace(hour=1)
    return start <= utc < end


def to_local(utc):
    """A UTC moment (naive UTC as the database keeps it, or aware) as London's wall clock (naive)."""
    if utc is None:
        return None
    if utc.tzinfo is not None:
        utc = utc.astimezone(timezone.utc).replace(tzinfo=None)
    return utc + timedelta(hours=1 if is_bst(utc) else 0)


def local(utc, fmt="%Y-%m-%d %H:%M"):
    """The Jinja filter `|local`: London's time, formatted; empty for None."""
    moment = to_local(utc)
    return moment.strftime(fmt) if moment else ""


def now_text(fmt="%Y-%m-%d %H:%M"):
    return local(datetime.now(timezone.utc), fmt)
