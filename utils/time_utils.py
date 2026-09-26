"""Date and duration parsing and formatting used by bot commands."""

from __future__ import annotations

import re
from datetime import date

# Human-readable summary of what `parse_hms_to_seconds` accepts, reused in
# command descriptions and error messages so they can never drift apart.
DURATION_HELP = (
    'Use units (`2h`, `15m`, `2h30m`, `2h 15m 25s`, `2h:15m:10s`) or `H:M:S` '
    '(`2:15:00`). A bare number needs a unit'
)
DATE_HELP = 'Use MM-DD or MM-DD-YYYY'

UNIT_SECONDS = {'h': 3600, 'm': 60, 's': 1}
UNIT_ORDER = ('h', 'm', 's')

_SEPARATORS = re.compile(r'[:\s]+')
_UNIT_PART = re.compile(r'(\d+)([hms])')
_UNIT_RUN = re.compile(r'(?:\d+[hms])+')


def parse_hms_to_seconds(value: str) -> int:
    """Parse a duration into seconds.

    Accepted forms, optionally prefixed with `+` or `-`. No command takes a
    negative duration, but parsing the sign lets them report "cannot be
    negative" instead of a confusing format error:

    * unit parts in descending order, run together or separated by spaces or
      colons — ``2h``, ``15m``, ``2h30m``, ``2h 15m 25s``, ``2h:15m:10s``
    * three colon-separated numbers as hours, minutes, seconds — ``2:15:00``

    A number with no unit is rejected, so ``120`` must be written ``120m``.

    Hours are not capped at 24, so ``36h`` and ``2000h`` are both fine, and
    minutes and seconds may overflow into the next unit up, making ``2h 90m``
    three and a half hours.
    """
    text = (value or '').strip()
    if not text:
        raise ValueError('Time must be a duration')

    sign = -1 if text[0] == '-' else 1
    if text[0] in '+-':
        text = text[1:].strip()

    parts = [part for part in _SEPARATORS.split(text) if part]
    if not parts:
        raise ValueError(DURATION_HELP)

    if all(part.isdigit() for part in parts):
        if len(parts) == 3:
            hours, minutes, seconds = (int(part) for part in parts)
            return sign * (hours * 3600 + minutes * 60 + seconds)
        raise ValueError(DURATION_HELP)

    total = 0
    previous_rank = -1
    for part in parts:
        part = part.lower()
        if not _UNIT_RUN.fullmatch(part):
            raise ValueError(DURATION_HELP)
        for amount, unit in _UNIT_PART.findall(part):
            rank = UNIT_ORDER.index(unit)
            if rank <= previous_rank:
                raise ValueError(DURATION_HELP)
            previous_rank = rank
            total += int(amount) * UNIT_SECONDS[unit]

    return sign * total


def parse_date_input(value: str, *, current_date: date | None = None) -> date:
    """Parse MM-DD or MM-DD-YYYY (also allowing slash separators).

    The month always comes first: a year-first ``2026-09-25`` is rejected,
    since its leading 2026 is not a month.
    """
    normalized = value.strip().replace('/', '-') if value else ''
    if not normalized:
        raise ValueError('Date is required')

    parts = normalized.split('-')
    try:
        if len(parts) == 2:
            month, day = (int(part) for part in parts)
            return date((current_date or date.today()).year, month, day)
        if len(parts) == 3:
            month, day, year = (int(part) for part in parts)
            return date(year, month, day)
    except ValueError as error:
        raise ValueError(DATE_HELP) from error
    raise ValueError(DATE_HELP)


def format_date(value: str | date) -> str:
    """Format a stored ISO date as `MM-DD-YYYY`, the order input is written in."""
    if isinstance(value, str):
        value = date.fromisoformat(value)
    return f'{value.month:02d}-{value.day:02d}-{value.year:04d}'


def format_seconds(total_seconds: float) -> str:
    """Format a duration as `2h:15m:25s`, always padding minutes and seconds.

    Every duration shown to a user goes through here, so the display format is
    defined in exactly one place.
    """
    total_seconds = int(round(total_seconds))
    sign = '-' if total_seconds < 0 else ''
    hours, remainder = divmod(abs(total_seconds), 3600)
    minutes, seconds = divmod(remainder, 60)
    return f'{sign}{hours}h:{minutes:02d}m:{seconds:02d}s'


def format_time(hours: float) -> str:
    """Format decimal hours as `2h:15m:25s`."""
    return format_seconds(hours * 3600)
