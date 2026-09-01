"""Session-specific SQL and display helpers."""

from collections.abc import Iterable, Mapping

from time_utils import format_time


def session_duration_seconds_sql(alias: str = 'sessions') -> str:
    """Return SQL that includes the elapsed time of a running session."""
    return f'''CASE
        WHEN {alias}.clock_in IS NOT NULL AND {alias}.clock_out IS NULL
            THEN {alias}.duration_seconds
                 + MAX(0, CAST((JULIANDAY(?) - JULIANDAY({alias}.clock_in)) * 86400 AS INTEGER))
        ELSE {alias}.duration_seconds
    END'''


def parse_session_ids(value: str) -> list[int]:
    """Parse comma-separated session IDs, optionally written with a # prefix."""
    try:
        ids = [int(item.strip().removeprefix('#')) for item in value.split(',') if item.strip()]
    except ValueError as error:
        raise ValueError('Session IDs must be numeric.') from error

    if len(ids) < 2 or len(ids) != len(set(ids)) or any(id_ <= 0 for id_ in ids):
        raise ValueError('Provide at least two unique, positive session IDs.')
    return ids


def build_session_list_fields(rows: Iterable[Mapping[str, object]]) -> list[tuple[str, str]]:
    """Group session rows by date and split field values within Discord's limit."""
    grouped: dict[str, list[Mapping[str, object]]] = {}
    for row in rows:
        grouped.setdefault(str(row['date']), []).append(row)

    fields = []
    for session_date, sessions in grouped.items():
        lines = []
        for session in sessions:
            state = f' ({session["state"]})' if session['state'] else ''
            lines.append(
                f'**#{session["id"]}** · {session["activity_name"]} — '
                f'{format_time(float(session["duration_seconds"]) / 3600)}{state}'
            )

        chunk: list[str] = []
        chunk_length = 0
        for line in lines:
            line_length = len(line) + 1
            if chunk and chunk_length + line_length > 1024:
                fields.append((session_date, '\n'.join(chunk)))
                chunk = []
                chunk_length = 0
            chunk.append(line)
            chunk_length += line_length
        if chunk:
            fields.append((session_date, '\n'.join(chunk)))
    return fields
