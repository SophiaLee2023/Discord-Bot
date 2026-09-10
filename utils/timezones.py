"""Timezone lookup, previewing, and per-user timezone resolution."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from functools import lru_cache
from zoneinfo import ZoneInfo, available_timezones

from utils import db

MAX_CHOICES = 25

# Shown before the user types anything, so the command previews a usable spread
# of zones rather than an arbitrary alphabetical slice.
POPULAR_ZONES = (
    'America/Los_Angeles',
    'America/Denver',
    'America/Chicago',
    'America/New_York',
    'America/Sao_Paulo',
    'Europe/London',
    'Europe/Paris',
    'Europe/Berlin',
    'Europe/Moscow',
    'Africa/Johannesburg',
    'Asia/Dubai',
    'Asia/Kolkata',
    'Asia/Shanghai',
    'Asia/Tokyo',
    'Asia/Seoul',
    'Australia/Sydney',
    'Pacific/Auckland',
    'UTC',
)


@lru_cache(maxsize=1)
def all_zones() -> tuple[str, ...]:
    """Every IANA zone name, sorted."""
    return tuple(sorted(available_timezones()))


def resolve(name: str) -> ZoneInfo | None:
    """Return the zone for `name`, matched case-insensitively, or None."""
    if not name:
        return None
    candidate = name.strip().replace(' ', '_')
    try:
        return ZoneInfo(candidate)
    except (KeyError, ValueError, ModuleNotFoundError, OSError):
        pass
    lowered = candidate.lower()
    for zone_name in all_zones():
        if zone_name.lower() == lowered:
            return ZoneInfo(zone_name)
    return None


def canonical_name(name: str) -> str | None:
    """Return the correctly-cased IANA name for `name`, or None if unknown."""
    zone = resolve(name)
    return str(zone) if zone else None


def offset_label(zone_name: str, now: datetime | None = None) -> str:
    """Render a zone's current UTC offset, e.g. 'UTC-07:00'."""
    reference = now or datetime.now()
    try:
        offset = ZoneInfo(zone_name).utcoffset(reference) or timedelta(0)
    except Exception:
        return 'UTC+00:00'
    total_minutes = int(offset.total_seconds() // 60)
    sign = '-' if total_minutes < 0 else '+'
    hours, minutes = divmod(abs(total_minutes), 60)
    return f'UTC{sign}{hours:02d}:{minutes:02d}'


def preview(zone_name: str, now: datetime | None = None) -> str:
    """Render a zone as 'Area/City — UTC-07:00 · Mon 2:31 PM' for previews."""
    reference = now or datetime.now()
    try:
        local = reference.astimezone(ZoneInfo(zone_name))
        clock = local.strftime('%a %I:%M %p').replace(' 0', ' ')
    except Exception:
        clock = '—'
    return f'{zone_name} — {offset_label(zone_name, reference)} · {clock}'


def search(current: str, limit: int = MAX_CHOICES) -> list[str]:
    """Rank zone names against a partial query for autocomplete."""
    needle = current.strip().lower().replace(' ', '_')
    if not needle:
        return list(POPULAR_ZONES)[:limit]

    starts_with, city_match, contains = [], [], []
    for zone_name in all_zones():
        lowered = zone_name.lower()
        if lowered.startswith(needle):
            starts_with.append(zone_name)
        elif lowered.rsplit('/', 1)[-1].startswith(needle):
            city_match.append(zone_name)
        elif needle in lowered:
            contains.append(zone_name)
        if len(starts_with) >= limit:
            break

    return (starts_with + city_match + contains)[:limit]


# --------------------------------------------------------------------------- #
# Per-user resolution
# --------------------------------------------------------------------------- #

def zone_for_user(user_id: int) -> ZoneInfo | None:
    """The user's configured zone, or None to fall back to server-local time."""
    name = db.get_user_timezone(user_id)
    return resolve(name) if name else None


def now_for_user(user_id: int) -> datetime:
    """Current wall-clock time in the user's timezone, as a naive datetime.

    Timestamps in the database are naive server-local values, so this is only
    used to decide which calendar day a moment belongs to.
    """
    zone = zone_for_user(user_id)
    if zone is None:
        return datetime.now()
    return datetime.now(zone).replace(tzinfo=None)


def today_for_user(user_id: int) -> date:
    """The current date in the user's timezone."""
    return now_for_user(user_id).date()
