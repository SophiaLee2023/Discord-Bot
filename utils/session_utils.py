"""Session-specific SQL and display helpers."""

from collections.abc import Iterable, Mapping

from utils.time_utils import format_date, format_seconds

# Discord allows far more, but a note is meant to be a label, not a paragraph.
MAX_NOTE_LENGTH = 500


def session_duration_seconds_sql(alias: str = 'sessions') -> str:
    """Return SQL that includes the elapsed time of a running session."""
    # Use REPLACE to convert ISO 'T' separator into a space so SQLite's JULIANDAY can parse it.
    return f'''CASE
        WHEN {alias}.clock_in IS NOT NULL AND {alias}.clock_out IS NULL
            THEN {alias}.duration_seconds
                 + MAX(0, CAST((JULIANDAY(REPLACE(?,'T',' ')) - JULIANDAY(REPLACE({alias}.clock_in,'T',' '))) * 86400 AS INTEGER))
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


def build_session_list_fields(rows: Iterable[Mapping[str, object]], hide_activity_name: bool = False) -> list[tuple[str, str]]:
    """Group session rows by date and split field values within Discord's limit.

    Rows are grouped on the stored ISO date so they keep the order the query
    returned them in, and only the field name is shown as `MM-DD-YYYY`.
    """
    grouped: dict[str, list[Mapping[str, object]]] = {}
    for row in rows:
        grouped.setdefault(str(row['date']), []).append(row)

    fields = []
    for session_date, sessions in grouped.items():
        label = format_date(session_date)
        lines = []
        for session in sessions:
            state = f' ({session["state"]})' if session['state'] else ''
            if hide_activity_name:
                entry = f'**#{session["id"]}** — {format_seconds(session["duration_seconds"])}{state}'
            else:
                entry = (
                    f'**#{session["id"]}** · {session["activity_name"]} — '
                    f'{format_seconds(session["duration_seconds"])}{state}'
                )
            if session['note']:
                entry += f'  • {session["note"]}'
            lines.append(entry)

        chunk: list[str] = []
        chunk_length = 0
        for line in lines:
            line_length = len(line) + 1
            if chunk and chunk_length + line_length > 1024:
                fields.append((label, '\n'.join(chunk)))
                chunk = []
                chunk_length = 0
            chunk.append(line)
            chunk_length += line_length
        if chunk:
            fields.append((label, '\n'.join(chunk)))
    return fields
