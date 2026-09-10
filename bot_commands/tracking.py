"""Clock in/out and the pause/resume/cancel controls."""

from __future__ import annotations

from datetime import datetime

import discord
from discord import app_commands

from utils import activities
from utils import db
from utils import discord_utils as ui
from utils import icons
from utils import permissions
from utils import timezones
from utils.session_utils import session_duration_seconds_sql
from utils.time_utils import format_seconds

# A session is "open" while it has never been clocked out and is either running
# (clock_in set) or paused (paused_at set).
OPEN_SESSION_WHERE = '''
    sessions.user_id = ?
    AND sessions.clock_out IS NULL
    AND (sessions.clock_in IS NOT NULL OR sessions.paused_at IS NOT NULL)
'''


def _open_session(conn, user_id: int):
    return conn.execute(
        f'''SELECT sessions.id, sessions.clock_in, sessions.clock_out,
                   sessions.duration_seconds, sessions.paused_at, activities.name
            FROM sessions
            JOIN activities ON sessions.activity_id = activities.id
            WHERE {OPEN_SESSION_WHERE}
            ORDER BY sessions.id DESC
            LIMIT 1''',
        (user_id,),
    ).fetchone()


def _elapsed_seconds(session, now: datetime) -> int:
    """Total tracked seconds for a session, including time since the last resume."""
    seconds = session['duration_seconds']
    if session['clock_in']:
        seconds += int(round((now - datetime.fromisoformat(session['clock_in'])).total_seconds()))
    return seconds


async def _resolve_target(
    interaction: discord.Interaction, user: discord.User | None, action: str
) -> discord.abc.User | None:
    """Return the member to act on, or None after replying about missing permission."""
    if user is None or user.id == interaction.user.id:
        return interaction.user
    if not permissions.is_admin(interaction):
        await ui.fail(interaction, f'Only admins can {action} other users!')
        return None
    return user


@app_commands.command(name='clockin', description='Clock in to an activity')
@app_commands.describe(
    activity_name='Optional: activity to track; defaults to the server default activity',
    user='Optional: mention a member to clock in (admin only)',
)
@app_commands.autocomplete(activity_name=activities.autocomplete)
async def clockin(
    interaction: discord.Interaction,
    activity_name: str | None = None,
    user: discord.User | None = None,
) -> None:
    target = await _resolve_target(interaction, user, 'clock in')
    if target is None:
        return

    with db.db(commit=True) as conn:
        try:
            activity = activities.resolve(conn, activity_name, interaction.guild)
        except activities.ActivityUnavailable as error:
            await ui.fail(interaction, str(error))
            return

        if _open_session(conn, target.id):
            await ui.fail(
                interaction, f'{target.mention} is already clocked in! Clock out first with `/clockout`'
            )
            return

        conn.execute(
            'INSERT INTO sessions (user_id, activity_id, date, duration_seconds, clock_in) VALUES (?, ?, ?, ?, ?)',
            (
                target.id,
                activity['id'],
                timezones.today_for_user(target.id).isoformat(),
                0,
                datetime.now().isoformat(),
            ),
        )

    embed = ui.notice(f'{target.mention} clocked in to **{activity["name"]}**', ui.GREEN)
    await ui.send_embed(interaction, embed, file=icons.apply_to_embed(embed, activity))


@app_commands.command(name='clockout', description='Clock out of your current activity')
@app_commands.describe(user='Optional: mention a member to clock out (admin only)')
async def clockout(interaction: discord.Interaction, user: discord.User | None = None) -> None:
    target = await _resolve_target(interaction, user, 'clock out')
    if target is None:
        return

    with db.db(commit=True) as conn:
        active = _open_session(conn, target.id)
        if not active:
            await ui.fail(interaction, f'{target.mention} is not clocked in!')
            return

        clock_out = datetime.now()
        seconds = _elapsed_seconds(active, clock_out)
        conn.execute(
            'UPDATE sessions SET clock_out = ?, paused_at = NULL, duration_seconds = ? WHERE id = ?',
            (clock_out.isoformat(), seconds, active['id']),
        )

        total = conn.execute(
            f'''SELECT SUM({session_duration_seconds_sql()}) AS total_seconds
                FROM sessions
                WHERE user_id = ? AND activity_id = (SELECT activity_id FROM sessions WHERE id = ?)''',
            (clock_out.isoformat(), target.id, active['id']),
        ).fetchone()

    embed = ui.notice(f'{target.mention} clocked out of **{active["name"]}**', ui.GREEN)
    embed.add_field(name='Session', value=format_seconds(seconds), inline=True)
    embed.add_field(name='Total', value=format_seconds(total['total_seconds'] or 0), inline=True)
    await ui.send_embed(interaction, embed)


@app_commands.command(name='pause', description='Pause your current clock')
async def pause(interaction: discord.Interaction) -> None:
    now = datetime.now()
    with db.db(commit=True) as conn:
        active = _open_session(conn, interaction.user.id)
        if not active:
            await ui.fail(interaction, 'You are not clocked in.')
            return
        if active['paused_at']:
            await ui.fail(interaction, 'Your clock is already paused.')
            return

        seconds = _elapsed_seconds(active, now)
        conn.execute(
            'UPDATE sessions SET duration_seconds = ?, clock_in = NULL, paused_at = ? WHERE id = ?',
            (seconds, now.isoformat(), active['id']),
        )

    await ui.send(
        interaction, f'Paused **{active["name"]}** at {format_seconds(seconds)}.', ephemeral=True
    )


@app_commands.command(name='resume', description='Resume your paused clock')
async def resume(interaction: discord.Interaction) -> None:
    with db.db(commit=True) as conn:
        paused = conn.execute(
            '''SELECT sessions.id, activities.name
               FROM sessions
               JOIN activities ON sessions.activity_id = activities.id
               WHERE sessions.user_id = ? AND sessions.clock_out IS NULL AND sessions.paused_at IS NOT NULL
               ORDER BY sessions.id DESC LIMIT 1''',
            (interaction.user.id,),
        ).fetchone()
        if not paused:
            await ui.fail(interaction, 'You do not have a paused clock.')
            return

        conn.execute(
            'UPDATE sessions SET clock_in = ?, paused_at = NULL WHERE id = ?',
            (datetime.now().isoformat(), paused['id']),
        )

    await ui.send(interaction, f'Resumed **{paused["name"]}**.', ephemeral=True)


@app_commands.command(
    name='cancel', description='Cancel and delete your active clock-in session (admin may specify a user)'
)
@app_commands.describe(user='Optional: mention a member to cancel their active session (admin only)')
async def cancel(interaction: discord.Interaction, user: discord.User | None = None) -> None:
    target = await _resolve_target(interaction, user, "cancel other users' sessions for")
    if target is None:
        return

    is_self = target.id == interaction.user.id
    with db.db(commit=True) as conn:
        active = _open_session(conn, target.id)
        if not active:
            whose = 'You have' if is_self else f'{target.mention} has'
            await ui.fail(interaction, f'{whose} no active session to cancel.')
            return
        conn.execute('DELETE FROM sessions WHERE id = ?', (active['id'],))

    message = (
        'Your active session has been cancelled and removed.'
        if is_self
        else f'Cancelled active session for {target.mention}.'
    )
    await ui.send(interaction, message, ephemeral=True)


def register(tree: app_commands.CommandTree) -> None:
    for command in (clockin, clockout, pause, resume, cancel):
        tree.add_command(command)
