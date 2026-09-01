"""Date and duration parsing and formatting used by bot commands."""

from datetime import date
import re


def parse_hms_to_seconds(hms: str) -> int:
    """Parse a signed duration into seconds.

    Accepts H:M:S (with optional h/m/s suffixes), M:S, or a single number of
    minutes. Minute and second values may overflow naturally into the next unit.
    """
    value = hms.strip() if hms else ''
    if not value:
        raise ValueError('Time must be a duration')

    sign = -1 if value.startswith('-') else 1
    if value[0] in '+-':
        value = value[1:]

    parts = value.split(':')
    if not 1 <= len(parts) <= 3:
        raise ValueError('Time must use minutes, M:S, or H:M:S')

    parsed_parts = []
    expected_suffixes = ('h', 'm', 's')[-len(parts):]
    for part, expected_suffix in zip(parts, expected_suffixes):
        match = re.fullmatch(r'(\d+)([hms]?)', part.strip().lower())
        if not match or (match.group(2) and match.group(2) != expected_suffix):
            raise ValueError('Time must use minutes, M:S, or H:M:S')
        parsed_parts.append(int(match.group(1)))

    if len(parsed_parts) == 1:
        return sign * parsed_parts[0] * 60
    if len(parsed_parts) == 2:
        minutes, seconds = parsed_parts
        return sign * (minutes * 60 + seconds)

    hours, minutes, seconds = parsed_parts
    return sign * (hours * 3600 + minutes * 60 + seconds)


def parse_date_input(value: str, *, current_date: date | None = None) -> date:
    """Parse YYYY-MM-DD, MM-DD, or MM-DD-YYYY (also allowing slash separators)."""
    normalized = value.strip().replace('/', '-') if value else ''
    if not normalized:
        raise ValueError('Date is required')

    parts = normalized.split('-')
    if len(parts) == 2:
        month, day = map(int, parts)
        return date((current_date or date.today()).year, month, day)
    if len(parts) == 3:
        if len(parts[0]) == 4:
            year, month, day = map(int, parts)
        else:
            month, day, year = map(int, parts)
        return date(year, month, day)
    raise ValueError('Date must be YYYY-MM-DD, MM-DD, or MM-DD-YYYY')


def format_time(hours: float) -> str:
    """Format decimal hours as a readable Hh:MMm:SSs duration."""
    total_seconds = int(round(hours * 3600))
    sign = '-' if total_seconds < 0 else ''
    total_seconds = abs(total_seconds)
    h = total_seconds // 3600
    m = (total_seconds % 3600) // 60
    s = total_seconds % 60
    return f'{sign}{h}h:{m:02d}m:{s:02d}s'
