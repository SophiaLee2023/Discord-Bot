def parse_hms_to_seconds(hms: str) -> int:
    """Parse H:M:S (optionally prefixed with + or -) to seconds (signed)."""
    value = hms.strip() if hms else ''
    if not value:
        raise ValueError('Time must be in H:M:S format')

    sign = -1 if value.startswith('-') else 1
    if value[0] in '+-':
        value = value[1:]

    parts = value.split(':')
    if len(parts) != 3 or not all(part.isdigit() for part in parts):
        raise ValueError('Time must be in H:M:S format')

    hours, minutes, seconds = map(int, parts)
    if minutes >= 60 or seconds >= 60:
        raise ValueError('Minutes and seconds must be less than 60')
    return sign * (hours * 3600 + minutes * 60 + seconds)


def format_time(hours: float) -> str:
    """Format decimal hours as a readable Hh:MMm:SSs duration."""
    total_seconds = int(round(hours * 3600))
    sign = '-' if total_seconds < 0 else ''
    total_seconds = abs(total_seconds)
    h = total_seconds // 3600
    m = (total_seconds % 3600) // 60
    s = total_seconds % 60
    return f'{sign}{h}h:{m:02d}m:{s:02d}s'
