"""Clock in/out and the pause/resume/cancel controls."""

from __future__ import annotations

import re
from datetime import datetime

import discord
from discord import app_commands

from utils import activities
from utils import db
from utils import discord_utils as ui
from utils import icons
from utils import timezones
from utils.session_utils import MAX_NOTE_LENGTH, session_duration_seconds_sql
from utils.time_utils import (
    DATE_HELP,
    DURATION_HELP,
    format_date,
    format_seconds,
    parse_date_input,
    parse_hms_to_seconds,
)

# A session is "open" while it has never been clocked out and is either running
# (clock_in set) or paused (paused_at set).
OPEN_SESSION_WHERE = '''
    sessions.user_id = ?
    AND sessions.clock_out IS NULL
    AND (sessions.clock_in IS NOT NULL OR sessions.paused_at IS NOT NULL)
'''

SESSION_COLUMNS = '''sessions.id, sessions.user_id, sessions.activity_id, sessions.date,
                     sessions.clock_in, sessions.clock_out, sessions.duration_seconds,
                     sessions.paused_at, sessions.note, sessions.channel_id,
                     sessions.message_id, activities.name'''

# The clock-out summary names its session in a field, and the buttons under it
# read the id back out of that field, so nothing about the message is stored.
SESSION_FIELD = 'Session #{id}'
_SESSION_FIELD_ID = re.compile(r'Session #(\d+)')


def _open_session(conn, user_id: int):
    return conn.execute(
        f'''SELECT {SESSION_COLUMNS}
            FROM sessions
            JOIN activities ON sessions.activity_id = activities.id
            WHERE {OPEN_SESSION_WHERE}
            ORDER BY sessions.id DESC
            LIMIT 1''',
        (user_id,),
    ).fetchone()


def _session_by_id(conn, session_id: int):
    return conn.execute(
        f'''SELECT {SESSION_COLUMNS}
            FROM sessions
            JOIN activities ON sessions.activity_id = activities.id
            WHERE sessions.id = ?''',
        (session_id,),
    ).fetchone()


def _session_in_summary(message: discord.Message | None) -> int | None:
    """The session id a clock-out summary message names in its first field."""
    if message is None or not message.embeds:
        return None
    for field in message.embeds[0].fields:
        found = _SESSION_FIELD_ID.fullmatch(field.name or '')
        if found:
            return int(found.group(1))
    return None


def _session_for_message(conn, message_id: int):
    """Fetch the session whose `/clockin` buttons live on this message.

    Buttons resolve their session from the message they are on rather than from
    anything held in memory, which is what lets them keep working after the bot
    restarts.
    """
    return conn.execute(
        f'''SELECT {SESSION_COLUMNS}
            FROM sessions
            JOIN activities ON sessions.activity_id = activities.id
            WHERE sessions.message_id = ?
            ORDER BY sessions.id DESC
            LIMIT 1''',
        (message_id,),
    ).fetchone()


def _relative(moment: datetime) -> str:
    """A Discord relative timestamp, which counts along in each client by itself.

    Writing the time this way means a running clock reads as "since 20 minutes
    ago" and stays right without the bot editing the message again.
    """
    return f'<t:{int(moment.timestamp())}:R>'


def _running_status(started: datetime, banked_seconds: int = 0) -> str:
    """Status for a clock that is running, counting from its last resume."""
    status = f'Running since {_relative(started)}'
    if banked_seconds:
        status += f' · {format_seconds(banked_seconds)} already tracked'
    return status


def _paused_status(seconds: int, moment: datetime) -> str:
    return f'Paused at {format_seconds(seconds)} · paused {_relative(moment)}'


def _jump_url(guild_id: int | None, channel_id: int, message_id: int) -> str:
    """Link to a message, using the DM form when there is no guild."""
    return f'https://discord.com/channels/{guild_id or "@me"}/{channel_id}/{message_id}'


def _elapsed_seconds(session, now: datetime) -> int:
    """Total tracked seconds for a session, including time since the last resume."""
    seconds = session['duration_seconds']
    if session['clock_in']:
        seconds += int(round((now - datetime.fromisoformat(session['clock_in'])).total_seconds()))
    return seconds


def _pause_session(conn, session, now: datetime) -> int:
    """Bank the elapsed time and stop the clock, returning the banked total."""
    seconds = _elapsed_seconds(session, now)
    conn.execute(
        'UPDATE sessions SET duration_seconds = ?, clock_in = NULL, paused_at = ? WHERE id = ?',
        (seconds, now.isoformat(), session['id']),
    )
    return seconds


def _resume_session(conn, session_id: int, now: datetime) -> None:
    conn.execute(
        'UPDATE sessions SET clock_in = ?, paused_at = NULL WHERE id = ?',
        (now.isoformat(), session_id),
    )


def _clock_out_session(conn, session, now: datetime) -> int:
    """Close a session, returning the duration it ended up with."""
    seconds = _elapsed_seconds(session, now)
    conn.execute(
        'UPDATE sessions SET clock_out = ?, paused_at = NULL, duration_seconds = ? WHERE id = ?',
        (now.isoformat(), seconds, session['id']),
    )
    return seconds


def _activity_total(conn, session) -> int:
    """Everything this session's owner has tracked against its activity."""
    row = conn.execute(
        f'''SELECT SUM({session_duration_seconds_sql()}) AS total_seconds
            FROM sessions
            WHERE user_id = ? AND activity_id = ?''',
        (datetime.now().isoformat(), session['user_id'], session['activity_id']),
    ).fetchone()
    return row['total_seconds'] or 0


def _clock_out_embed(user: discord.abc.User, session, total_seconds: int) -> discord.Embed:
    """The summary posted as its own message whenever a session is clocked out.

    Everything comes from the stored row, so the buttons under it can rebuild
    the same embed after an edit instead of patching bits of the old one.
    """
    embed = ui.notice(f'{user.mention} clocked out of **{session["name"]}**', ui.GREEN)
    # The id is shown, and read back by the buttons, so the session can be
    # edited, tagged or removed right away.
    embed.add_field(
        name=SESSION_FIELD.format(id=session['id']),
        value=format_seconds(session['duration_seconds']),
        inline=True,
    )
    embed.add_field(name='Total', value=format_seconds(total_seconds), inline=True)
    embed.add_field(name='Date', value=format_date(session['date']), inline=True)
    if session['note']:
        embed.add_field(name='Note', value=session['note'], inline=False)
    embed.set_footer(text=f'Use id {session["id"]} to edit, tag, or remove this session.')
    return embed


def _status_embed(message: discord.Message | None, status: str, colour: discord.Color) -> discord.Embed:
    """Write a status into the clock-in embed, leaving the rest of it alone."""
    embed = message.embeds[0] if message is not None and message.embeds else ui.notice('Clock', colour)
    embed.colour = colour
    if embed.fields:
        embed.set_field_at(0, name='Status', value=status, inline=False)
    else:
        embed.add_field(name='Status', value=status, inline=False)
    return embed


def _icon_attachment(session) -> tuple[discord.File | None, str | None]:
    """A fresh upload of the session activity's icon, with the URL to show it."""
    row = db.query_one(
        f'SELECT id, {icons.ICON_COLUMNS} FROM activities WHERE id = ?', (session['activity_id'],)
    )
    return icons.attachment(row)


def _clock_edit(session, message: discord.Message | None, status: str, colour: discord.Color, *,
                paused: bool | None) -> dict:
    """Everything an edit of the clock-in message needs. `paused` None drops the buttons.

    The icon is uploaded again on every edit. An `attachment://` link only
    resolves against files sent in the same request, so a file merely carried
    over is no longer part of the embed and Discord shows it underneath as a
    plain attachment instead. Rewriting the image and the attachment together
    also means the message follows an activity whose icon has since changed.
    """
    embed = _status_embed(message, status, colour)
    icon, url = _icon_attachment(session) if session is not None else (None, None)
    embed.set_image(url=url)
    return {
        'embed': embed,
        'view': None if paused is None else SessionControls(paused=paused),
        'attachments': [icon] if icon is not None else [],
    }


class SessionControls(discord.ui.View):
    """Clock out, pause and resume buttons on the message `/clockin` posts.

    The view holds no session state: every press looks the session up by the id
    of the message it was pressed on. One instance registered at startup with
    `Client.add_view` therefore drives every clock-in message ever posted, so
    the buttons work again after a restart. The `paused` flag only decides which
    buttons a freshly rendered message shows.
    """

    def __init__(self, *, paused: bool = False) -> None:
        super().__init__(timeout=None)
        self.pause_button.disabled = paused
        self.resume_button.disabled = not paused

    async def _session(self, interaction: discord.Interaction):
        """Return the session this press applies to, or None having replied."""
        with db.db() as conn:
            session = _session_for_message(conn, interaction.message.id)

        if session is None or session['clock_out'] is not None:
            # Clocked out, cancelled, or from a database that no longer has it.
            await interaction.response.edit_message(
                **_clock_edit(session, interaction.message, 'Clocked out', ui.GREY, paused=None)
            )
            return None
        if interaction.user.id != session['user_id']:
            await ui.fail(interaction, 'Only the member who clocked in can use these buttons.')
            return None
        return session

    @discord.ui.button(label='Clock out', style=discord.ButtonStyle.primary, custom_id='clockin:out')
    async def clock_out_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        session = await self._session(interaction)
        if session is None:
            return

        with db.db(commit=True) as conn:
            seconds = _clock_out_session(conn, session, datetime.now())
            closed = _session_by_id(conn, session['id'])
            total = _activity_total(conn, closed)

        await interaction.response.edit_message(
            **_clock_edit(
                closed, interaction.message, f'Clocked out at {format_seconds(seconds)}', ui.GREY, paused=None
            )
        )
        # Clocking out still announces itself in a new message, button or command.
        await interaction.followup.send(
            embed=_clock_out_embed(interaction.user, closed, total), view=SessionSummary()
        )

    @discord.ui.button(label='Pause', style=discord.ButtonStyle.secondary, custom_id='clockin:pause')
    async def pause_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        session = await self._session(interaction)
        if session is None:
            return

        with db.db(commit=True) as conn:
            if session['paused_at']:
                # Already paused, so report when it stopped rather than now.
                paused_at = datetime.fromisoformat(session['paused_at'])
                seconds = session['duration_seconds']
            else:
                paused_at = datetime.now()
                seconds = _pause_session(conn, session, paused_at)

        await interaction.response.edit_message(
            **_clock_edit(
                session, interaction.message, _paused_status(seconds, paused_at), ui.GREY, paused=True
            )
        )

    @discord.ui.button(
        label='Resume', style=discord.ButtonStyle.success, custom_id='clockin:resume', disabled=True
    )
    async def resume_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        session = await self._session(interaction)
        if session is None:
            return

        with db.db(commit=True) as conn:
            if session['paused_at']:
                started = datetime.now()
                _resume_session(conn, session['id'], started)
            else:
                # Already running: leave the clock alone and just redraw it.
                started = datetime.fromisoformat(session['clock_in'])

        await interaction.response.edit_message(
            **_clock_edit(
                session,
                interaction.message,
                _running_status(started, session['duration_seconds']),
                ui.GREEN,
                paused=False,
            )
        )


    @discord.ui.button(label='Cancel', style=discord.ButtonStyle.danger, custom_id='clockin:cancel')
    async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        session = await self._session(interaction)
        if session is None:
            return

        with db.db(commit=True) as conn:
            conn.execute('DELETE FROM sessions WHERE id = ?', (session['id'],))

        await interaction.response.edit_message(
            **_clock_edit(session, interaction.message, 'Cancelled · nothing was recorded', ui.GREY, paused=None)
        )


class _SummaryModal(discord.ui.Modal):
    """Base for the popups behind the clock-out summary buttons.

    Both need the same three steps: find the session the summary names, refuse
    anyone who does not own it, then redraw that summary from the stored row.
    """

    def __init__(self, session_id: int, *, title: str) -> None:
        super().__init__(title=title)
        self.session_id = session_id

    async def _apply(self, interaction: discord.Interaction, updates: dict) -> None:
        with db.db(commit=True) as conn:
            session = _session_by_id(conn, self.session_id)
            if session is None:
                await ui.fail(interaction, 'That session no longer exists.')
                return
            if session['user_id'] != interaction.user.id:
                await ui.fail(interaction, 'You can only change your own sessions.')
                return

            assignments = ', '.join(f'{column} = ?' for column in updates)
            conn.execute(
                f'UPDATE sessions SET {assignments} WHERE id = ?',
                (*updates.values(), self.session_id),
            )
            edited = _session_by_id(conn, self.session_id)
            total = _activity_total(conn, edited)

        await interaction.response.edit_message(
            embed=_clock_out_embed(interaction.user, edited, total), view=SessionSummary()
        )


class EditSessionModal(_SummaryModal):
    """Change a finished session's date, its duration, or both."""

    def __init__(self, session) -> None:
        super().__init__(session['id'], title='Edit session')
        self.date = discord.ui.TextInput(
            label='Date',
            default=format_date(session['date']),
            placeholder='MM-DD or MM-DD-YYYY',
            required=False,
        )
        self.duration = discord.ui.TextInput(
            label='Duration',
            default=format_seconds(session['duration_seconds']),
            placeholder='2h, 15m, 2h30m or 2:15:00',
            required=False,
        )
        self.add_item(self.date)
        self.add_item(self.duration)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        updates: dict = {}

        if self.date.value.strip():
            try:
                parsed = parse_date_input(
                    self.date.value, current_date=timezones.today_for_user(interaction.user.id)
                )
            except ValueError:
                await ui.fail(interaction, f'Invalid date. {DATE_HELP}')
                return
            updates['date'] = parsed.isoformat()

        if self.duration.value.strip():
            try:
                seconds = parse_hms_to_seconds(self.duration.value)
            except ValueError:
                await ui.fail(interaction, f'Invalid duration. {DURATION_HELP}')
                return
            if seconds < 0:
                await ui.fail(interaction, 'Session duration cannot be negative.')
                return
            updates['duration_seconds'] = seconds

        if not updates:
            await ui.fail(interaction, 'Give a date or a duration to change.')
            return
        await self._apply(interaction, updates)


class NoteSessionModal(_SummaryModal):
    """Attach or replace the note shown with a finished session."""

    def __init__(self, session) -> None:
        super().__init__(session['id'], title='Session note')
        self.note = discord.ui.TextInput(
            label='Note',
            default=session['note'] or None,
            placeholder='What was this session?',
            max_length=MAX_NOTE_LENGTH,
            required=False,
            style=discord.TextStyle.paragraph,
        )
        self.add_item(self.note)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        note = self.note.value.strip()
        await self._apply(interaction, {'note': note or None})


class SessionSummary(discord.ui.View):
    """Edit and Note buttons under the message clocking out posts.

    Like the clock-in controls this holds no state, reading the session id back
    out of the summary it is attached to, so the buttons still work after a
    restart with nothing extra stored.
    """

    def __init__(self) -> None:
        super().__init__(timeout=None)

    async def _session(self, interaction: discord.Interaction):
        """Return the session this summary is about, or None having replied."""
        session_id = _session_in_summary(interaction.message)
        with db.db() as conn:
            session = _session_by_id(conn, session_id) if session_id else None

        if session is None:
            await ui.fail(interaction, 'That session no longer exists.')
            return None
        if interaction.user.id != session['user_id']:
            await ui.fail(interaction, 'You can only change your own sessions.')
            return None
        return session

    @discord.ui.button(label='Edit', style=discord.ButtonStyle.secondary, custom_id='summary:edit')
    async def edit_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        session = await self._session(interaction)
        if session is not None:
            await interaction.response.send_modal(EditSessionModal(session))

    @discord.ui.button(label='Note', style=discord.ButtonStyle.secondary, custom_id='summary:note')
    async def note_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        session = await self._session(interaction)
        if session is not None:
            await interaction.response.send_modal(NoteSessionModal(session))


async def _update_clock_message(
    client: discord.Client, session, status: str, colour: discord.Color, *, paused: bool | None
) -> None:
    """Show `status` on the clock-in message after a command changed the clock.

    `paused` of None means the session is over, so the buttons come off. Every
    failure is ignored: a deleted message or an unreachable channel must not
    turn a working command into an error.
    """
    if not session['message_id'] or not session['channel_id']:
        return

    channel = client.get_channel(session['channel_id'])
    if not isinstance(channel, discord.abc.Messageable):
        return

    try:
        message = await channel.fetch_message(session['message_id'])
        await message.edit(**_clock_edit(session, message, status, colour, paused=paused))
    except discord.HTTPException:
        pass


async def _report_open_session(interaction: discord.Interaction, session) -> None:
    """Answer a second `/clockin` with the clock already running.

    Rather than only refusing, this says which activity is open and how long it
    has been going, points at the message holding its buttons, and asks for a
    resume rather than a clock out when the clock is merely paused.
    """
    if session['paused_at']:
        status = _paused_status(session['duration_seconds'], datetime.fromisoformat(session['paused_at']))
        advice = 'Resume it with `/resume`, or clock out with `/clockout`.'
    else:
        status = _running_status(datetime.fromisoformat(session['clock_in']), session['duration_seconds'])
        advice = 'Clock out with `/clockout` when you are done.'

    embed = ui.notice(
        f'You are already clocked in to **{session["name"]}**. {advice}', ui.GREY
    )
    embed.add_field(name='Status', value=status, inline=False)

    # A link button rather than working controls: the buttons that drive this
    # session live on its own clock-in message, and only ever on that one.
    view = None
    if session['channel_id'] and session['message_id']:
        view = discord.ui.View(timeout=None)
        view.add_item(
            discord.ui.Button(
                label='Go to your clock',
                style=discord.ButtonStyle.link,
                url=_jump_url(interaction.guild_id, session['channel_id'], session['message_id']),
            )
        )
    await ui.send_embed(interaction, embed, view=view)


@app_commands.command(name='clockin', description='Clock in to an activity')
@app_commands.describe(
    activity_name='Optional: activity to track; defaults to the server default activity'
)
@app_commands.autocomplete(activity_name=activities.autocomplete)
async def clockin(interaction: discord.Interaction, activity_name: str | None = None) -> None:
    with db.db(commit=True) as conn:
        try:
            activity = activities.resolve(conn, activity_name, interaction.guild)
        except activities.ActivityUnavailable as error:
            await ui.fail(interaction, str(error))
            return

        open_session = _open_session(conn, interaction.user.id)
        if open_session:
            await _report_open_session(interaction, open_session)
            return

        started = datetime.now()
        session_id = conn.execute(
            'INSERT INTO sessions (user_id, activity_id, date, duration_seconds, clock_in) VALUES (?, ?, ?, ?, ?)',
            (
                interaction.user.id,
                activity['id'],
                timezones.today_for_user(interaction.user.id).isoformat(),
                0,
                started.isoformat(),
            ),
        ).lastrowid

    embed = ui.notice(f'{interaction.user.mention} clocked in to **{activity["name"]}**', ui.GREEN)
    embed.add_field(name='Status', value=_running_status(started), inline=False)
    icon = icons.apply_to_embed(embed, activity)
    await ui.send_embed(interaction, embed, file=icon, view=SessionControls())

    # Remember where the buttons are so a later run of the bot, and the pause and
    # resume commands, can both find the message again.
    try:
        message = await interaction.original_response()
    except discord.HTTPException:
        return
    db.execute(
        'UPDATE sessions SET channel_id = ?, message_id = ? WHERE id = ?',
        (message.channel.id, message.id, session_id),
    )


@app_commands.command(name='clockout', description='Clock out of your current activity')
async def clockout(interaction: discord.Interaction) -> None:
    with db.db(commit=True) as conn:
        active = _open_session(conn, interaction.user.id)
        if not active:
            await ui.fail(interaction, 'You are not clocked in!')
            return
        seconds = _clock_out_session(conn, active, datetime.now())
        closed = _session_by_id(conn, active['id'])
        total = _activity_total(conn, closed)

    await ui.send_embed(
        interaction, _clock_out_embed(interaction.user, closed, total), view=SessionSummary()
    )
    await _update_clock_message(
        interaction.client, active, f'Clocked out at {format_seconds(seconds)}', ui.GREY, paused=None
    )


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

        seconds = _pause_session(conn, active, now)

    await ui.send(interaction, f'Paused **{active["name"]}** at {format_seconds(seconds)}.')
    await _update_clock_message(
        interaction.client, active, _paused_status(seconds, now), ui.GREY, paused=True
    )


@app_commands.command(name='resume', description='Resume your paused clock')
async def resume(interaction: discord.Interaction) -> None:
    with db.db(commit=True) as conn:
        paused = conn.execute(
            f'''SELECT {SESSION_COLUMNS}
                FROM sessions
                JOIN activities ON sessions.activity_id = activities.id
                WHERE sessions.user_id = ? AND sessions.clock_out IS NULL AND sessions.paused_at IS NOT NULL
                ORDER BY sessions.id DESC LIMIT 1''',
            (interaction.user.id,),
        ).fetchone()
        if not paused:
            await ui.fail(interaction, 'You do not have a paused clock.')
            return

        started = datetime.now()
        _resume_session(conn, paused['id'], started)

    await ui.send(interaction, f'Resumed **{paused["name"]}**.')
    await _update_clock_message(
        interaction.client,
        paused,
        _running_status(started, paused['duration_seconds']),
        ui.GREEN,
        paused=False,
    )


@app_commands.command(name='cancel', description='Cancel and delete your active clock-in session')
async def cancel(interaction: discord.Interaction) -> None:
    with db.db(commit=True) as conn:
        active = _open_session(conn, interaction.user.id)
        if not active:
            await ui.fail(interaction, 'You have no active session to cancel.')
            return
        conn.execute('DELETE FROM sessions WHERE id = ?', (active['id'],))

    await ui.send(interaction, 'Your active session has been cancelled and removed.')
    await _update_clock_message(interaction.client, active, 'Session cancelled', ui.GREY, paused=None)


def register(tree: app_commands.CommandTree) -> None:
    for command in (clockin, clockout, pause, resume, cancel):
        tree.add_command(command)
