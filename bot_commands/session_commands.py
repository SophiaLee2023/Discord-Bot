"""/session — list, tag, combine, add, remove, and edit recorded sessions."""

from __future__ import annotations

from datetime import datetime

import discord
from discord import app_commands

from utils import activities, db
from utils import discord_utils as ui
from utils import permissions, timezones
from utils.session_utils import (
    MAX_NOTE_LENGTH,
    build_session_list_fields,
    parse_session_ids,
    session_duration_seconds_sql,
)
from utils.time_utils import (
    DATE_HELP,
    DURATION_HELP,
    format_date,
    format_seconds,
    parse_date_input,
    parse_hms_to_seconds,
)

group = app_commands.Group(name='session', description='Manage sessions')


def _may_edit(interaction: discord.Interaction, owner_id: int) -> bool:
    return owner_id == interaction.user.id or permissions.is_admin(interaction)


@group.command(name='list', description='List your recorded sessions')
async def session_list(interaction: discord.Interaction) -> None:
    rows = db.query_all(
        f'''SELECT sessions.id,
                   sessions.date,
                   activities.name AS activity_name,
                   {session_duration_seconds_sql()} AS duration_seconds,
                   sessions.note,
                   CASE
                       WHEN sessions.paused_at IS NOT NULL AND sessions.clock_out IS NULL THEN 'Paused'
                       WHEN sessions.clock_in IS NOT NULL AND sessions.clock_out IS NULL THEN 'In progress'
                   END AS state
            FROM sessions
            JOIN activities ON sessions.activity_id = activities.id
            WHERE sessions.user_id = ?
            ORDER BY sessions.date DESC, sessions.id DESC''',
        (datetime.now().isoformat(), interaction.user.id),
    )

    if not rows:
        await ui.send(interaction, 'No sessions found.', color=ui.GREY)
        return

    single_activity = len({row['activity_name'] for row in rows}) == 1
    fields = [
        (f'📅 {label}', value)
        for label, value in build_session_list_fields(rows, hide_activity_name=single_activity)
    ]

    embeds = ui.paginate_fields(
        fields,
        lambda: discord.Embed(
            title=f'Sessions for {interaction.user.display_name}',
            description='Each entry shows **ID** · activity — duration.',
            color=ui.BLURPLE,
        ),
    )
    await interaction.response.send_message(embeds=embeds)


@group.command(name='tag', description='Add a note to a session by its numeric id')
@app_commands.describe(note='Note to display with the session', id='Session id to tag')
async def session_tag(interaction: discord.Interaction, note: str, id: int) -> None:
    note = note.strip()
    if not note:
        await ui.fail(interaction, 'A session note cannot be empty.')
        return
    if len(note) > MAX_NOTE_LENGTH:
        await ui.fail(interaction, f'Keep session notes to {MAX_NOTE_LENGTH} characters or fewer.')
        return

    with db.db(commit=True) as conn:
        row = conn.execute('SELECT id, user_id FROM sessions WHERE id = ?', (id,)).fetchone()
        if not row:
            await ui.fail(interaction, 'Session ID not found.')
            return
        if not _may_edit(interaction, row['user_id']):
            await ui.fail(interaction, 'You can only tag your own sessions unless you are an admin.')
            return

        conn.execute('UPDATE sessions SET note = ? WHERE id = ?', (note, id))

    await ui.send(interaction, f'Added a note to session #{id}.')


@group.command(name='combine', description='Combine sessions with matching activity and date')
@app_commands.describe(ids='Comma-separated session IDs, for example: 11, 12, 13')
async def session_combine(interaction: discord.Interaction, ids: str) -> None:
    try:
        session_ids = parse_session_ids(ids)
    except ValueError as error:
        await ui.fail(interaction, str(error))
        return

    with db.db(commit=True) as conn:
        placeholders = ', '.join('?' * len(session_ids))
        rows = conn.execute(
            f'''SELECT id, user_id, activity_id, date, duration_seconds, clock_in, clock_out, paused_at
                FROM sessions WHERE id IN ({placeholders})''',
            session_ids,
        ).fetchall()

        if len(rows) != len(session_ids):
            await ui.fail(interaction, 'One or more session IDs were not found.')
            return
        # Manually added sessions have no timestamps at all; only reject ones
        # that are still running or paused.
        if any(
            row['clock_out'] is None and (row['clock_in'] is not None or row['paused_at'] is not None)
            for row in rows
        ):
            await ui.fail(interaction, 'Clock out active or paused sessions before combining them.')
            return

        owners = {row['user_id'] for row in rows}
        if len(owners) != 1 or len({row['activity_id'] for row in rows}) != 1 or len({row['date'] for row in rows}) != 1:
            await ui.fail(interaction, 'Sessions must belong to one user and have the same activity and date.')
            return
        if not _may_edit(interaction, next(iter(owners))):
            await ui.fail(interaction, 'You can only combine your own sessions unless you are an admin.')
            return

        anchor_id = min(session_ids)
        total_seconds = sum(row['duration_seconds'] for row in rows)
        others = [session_id for session_id in session_ids if session_id != anchor_id]
        conn.execute('UPDATE sessions SET duration_seconds = ? WHERE id = ?', (total_seconds, anchor_id))
        conn.execute(f'DELETE FROM sessions WHERE id IN ({", ".join("?" * len(others))})', others)

    await ui.send(
        interaction,
        f'Combined {len(session_ids)} sessions into #{anchor_id} ({format_seconds(total_seconds)}).',
    )


@group.command(name='add', description='Add a session for a date')
@app_commands.describe(
    duration='Duration: 2h, 15m, 2h30m, 2h 15m 25s, or 2:15:00',
    date_str='Date: MM-DD or MM-DD-YYYY. Defaults to today',
    activity_name='Optional: activity for the session; defaults to the server default activity',
)
@app_commands.autocomplete(activity_name=activities.autocomplete)
async def session_add(
    interaction: discord.Interaction,
    duration: str,
    date_str: str | None = None,
    activity_name: str | None = None,
) -> None:
    try:
        seconds = parse_hms_to_seconds(duration)
    except ValueError:
        await ui.fail(interaction, f'Invalid duration. {DURATION_HELP}')
        return
    if seconds < 0:
        await ui.fail(interaction, 'Session duration cannot be negative.')
        return

    if date_str:
        try:
            session_date = parse_date_input(
                date_str, current_date=timezones.today_for_user(interaction.user.id)
            )
        except ValueError:
            await ui.fail(interaction, f'Invalid date. {DATE_HELP}')
            return
    else:
        session_date = timezones.today_for_user(interaction.user.id)

    with db.db(commit=True) as conn:
        try:
            activity = activities.resolve(conn, activity_name, interaction.guild)
        except activities.ActivityUnavailable as error:
            await ui.fail(interaction, str(error))
            return

        session_id = conn.execute(
            'INSERT INTO sessions (user_id, activity_id, date, duration_seconds) VALUES (?, ?, ?, ?)',
            (interaction.user.id, activity['id'], session_date.isoformat(), seconds),
        ).lastrowid

    await ui.send(
        interaction,
        f'Session #{session_id} added on {format_date(session_date)} '
        f'({format_seconds(seconds)} of **{activity["name"]}**)',
    )


@group.command(name='remove', description='Remove one of your own sessions by its numeric id')
@app_commands.describe(id='Session id to remove')
async def session_remove(interaction: discord.Interaction, id: int) -> None:
    with db.db(commit=True) as conn:
        row = conn.execute('SELECT id, user_id FROM sessions WHERE id = ?', (id,)).fetchone()
        if not row:
            await ui.fail(interaction, 'Session id not found')
            return
        # Deleting is the one action an admin cannot take for someone else: it
        # always applies to the caller's own sessions.
        if row['user_id'] != interaction.user.id:
            await ui.fail(interaction, 'You can only remove your own sessions.')
            return
        conn.execute('DELETE FROM sessions WHERE id = ?', (id,))

    await ui.send(interaction, f'Removed session #{id}.')


@group.command(name='edit', description='Edit a session by id (change date and/or duration)')
@app_commands.describe(
    id='Session id to edit',
    date='Optional new date: MM-DD or MM-DD-YYYY',
    duration='Optional duration: 2h, 15m, 2h30m, 2h 15m 25s, or 2:15:00',
)
async def session_edit(
    interaction: discord.Interaction, id: int, date: str | None = None, duration: str | None = None
) -> None:
    if date is None and duration is None:
        await ui.fail(interaction, 'Nothing to update. Provide `date` and/or `duration`.')
        return

    updates: list[str] = []
    params: list[object] = []

    if date is not None:
        try:
            params.append(parse_date_input(date, current_date=timezones.today_for_user(interaction.user.id)).isoformat())
        except ValueError:
            await ui.fail(interaction, f'Invalid date. {DATE_HELP}')
            return
        updates.append('date = ?')

    if duration is not None:
        try:
            seconds = parse_hms_to_seconds(duration)
        except ValueError:
            await ui.fail(interaction, f'Invalid duration. {DURATION_HELP}')
            return
        if seconds < 0:
            await ui.fail(interaction, 'Session duration cannot be negative.')
            return
        updates.append('duration_seconds = ?')
        params.append(seconds)

    with db.db(commit=True) as conn:
        row = conn.execute('SELECT id, user_id FROM sessions WHERE id = ?', (id,)).fetchone()
        if not row:
            await ui.fail(interaction, 'Session id not found')
            return
        if not _may_edit(interaction, row['user_id']):
            await ui.fail(interaction, 'You can only edit your own sessions unless you are an admin.')
            return
        conn.execute(f'UPDATE sessions SET {", ".join(updates)} WHERE id = ?', (*params, id))

    await ui.send(interaction, f'Session #{id} updated.')


def register(tree: app_commands.CommandTree) -> None:
    tree.add_command(group)
