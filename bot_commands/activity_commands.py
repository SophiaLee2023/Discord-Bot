"""/activity — manage the list of trackable activities."""

from __future__ import annotations

from datetime import datetime

import discord
from discord import app_commands

from utils import activities
from utils import db
from utils import discord_utils as ui
from utils import icons
from utils import permissions
from utils import session_utils
from utils.time_utils import format_time

group = app_commands.Group(name='activity', description='Manage activities')


@group.command(name='add', description='Add a new activity/commitment to track')
@app_commands.describe(name='Name of the new activity')
async def activity_add(interaction: discord.Interaction, name: str) -> None:
    if not permissions.is_admin(interaction):
        await ui.fail(interaction, 'You need to be an admin to use this command!')
        return

    name = name.strip()
    if not name:
        await ui.fail(interaction, 'An activity name cannot be empty.')
        return

    if db.query_one('SELECT id FROM activities WHERE LOWER(name) = LOWER(?)', (name,)):
        await ui.fail(interaction, f'Activity **{name}** already exists!')
        return

    db.execute('INSERT INTO activities (name) VALUES (?)', (name,))
    await ui.send(interaction, f'Activity **{name}** added!')


@group.command(name='remove', description='Remove an activity')
@app_commands.describe(name='Name of the activity to remove')
@app_commands.autocomplete(name=activities.autocomplete)
async def activity_remove(interaction: discord.Interaction, name: str) -> None:
    if not permissions.is_admin(interaction):
        await ui.fail(interaction, 'You need to be an admin to use this command!')
        return

    row = db.query_one('SELECT id FROM activities WHERE LOWER(name) = LOWER(?)', (name,))
    if not row:
        await ui.fail(interaction, f'Activity **{name}** not found!')
        return

    db.execute('DELETE FROM activities WHERE id = ?', (row['id'],))
    await ui.send(interaction, f'Activity **{name}** removed!')


@group.command(name='list', description='List all activities')
async def activity_list(interaction: discord.Interaction) -> None:
    rows = db.query_all('SELECT name FROM activities ORDER BY name')
    if not rows:
        await ui.send(
            interaction, 'No activities found. Add one with `/activity add`', color=ui.GREY
        )
        return

    embed = discord.Embed(title='Activities', color=ui.BLURPLE)
    embed.description = '\n'.join(f'• {row["name"]}' for row in rows)
    await interaction.response.send_message(embed=embed)


@group.command(name='icon', description='Set an icon image for an activity')
@app_commands.describe(name='Name of the activity', image='Image to use as icon')
@app_commands.autocomplete(name=activities.autocomplete)
async def activity_icon(
    interaction: discord.Interaction, name: str, image: discord.Attachment
) -> None:
    if not permissions.is_admin(interaction):
        await ui.fail(interaction, 'You need to be an admin to use this command!')
        return

    if not image.content_type or not image.content_type.startswith('image/'):
        await ui.fail(interaction, 'Please attach an image file!')
        return

    if image.size > icons.MAX_ICON_BYTES:
        limit_mb = icons.MAX_ICON_BYTES // (1024 * 1024)
        await ui.fail(interaction, f'That image is too large. Keep icons under {limit_mb} MB.')
        return

    row = db.query_one('SELECT id FROM activities WHERE LOWER(name) = LOWER(?)', (name,))
    if not row:
        await ui.fail(interaction, f'Activity **{name}** not found!')
        return

    await interaction.response.defer()
    icons.store(row['id'], image.filename, await image.read())

    stored = db.query_one(f'SELECT {activities.SELECT_COLUMNS} FROM activities WHERE id = ?', (row['id'],))
    embed = ui.notice(f'Icon set for **{name}**!', ui.GREEN)
    await ui.send_embed(interaction, embed, file=icons.apply_to_embed(embed, stored))


@group.command(name='default', description='Set or view the guild default activity')
@app_commands.describe(name='Name of the activity to set as default (omit to view)')
@app_commands.autocomplete(name=activities.autocomplete)
async def activity_default(interaction: discord.Interaction, name: str | None = None) -> None:
    if interaction.guild is None:
        await ui.fail(interaction, 'This command only works in a server.')
        return

    if name is None:
        default_id = db.get_default_activity_id(interaction.guild.id)
        row = (
            db.query_one(f'SELECT {activities.SELECT_COLUMNS} FROM activities WHERE id = ?', (default_id,))
            if default_id
            else None
        )
        if not row:
            await ui.send(interaction, 'No default activity set for this server.', color=ui.GREY)
            return

        embed = discord.Embed(title='Default Activity', description=f'**{row["name"]}**', color=ui.BLURPLE)
        await ui.send_embed(interaction, embed, file=icons.apply_to_embed(embed, row))
        return

    if not permissions.is_admin(interaction):
        await ui.fail(interaction, 'You need to be an admin to set the default activity.')
        return

    row = db.query_one('SELECT id FROM activities WHERE LOWER(name) = LOWER(?)', (name,))
    if not row:
        await ui.fail(interaction, f'Activity **{name}** not found!')
        return

    db.set_default_activity_id(interaction.guild.id, row['id'])
    await ui.send(interaction, f'Default activity set to **{name}**')


@group.command(name='if', description='Estimate earnings for an activity at a given hourly wage')
@app_commands.describe(
    wage='Hourly wage (e.g. 10.5)',
    activity_name='Optional: activity to evaluate; defaults to the server default activity',
)
@app_commands.autocomplete(activity_name=activities.autocomplete)
async def activity_if(
    interaction: discord.Interaction, wage: float, activity_name: str | None = None
) -> None:
    with db.db() as conn:
        try:
            activity = activities.resolve(conn, activity_name, interaction.guild)
        except activities.ActivityUnavailable as error:
            await ui.fail(interaction, str(error))
            return

        rows = conn.execute(
            f'''SELECT user_id, SUM({session_utils.session_duration_seconds_sql()}) AS seconds
                FROM sessions
                WHERE activity_id = ?
                GROUP BY user_id
                ORDER BY seconds DESC''',
            (datetime.now().isoformat(), activity['id']),
        ).fetchall()

    if not rows:
        await ui.fail(interaction, f'No recorded sessions for **{activity["name"]}**')
        return

    lines = [f'If for **{activity["name"]}** at ${wage:.2f}/hr:']
    for row in rows:
        hours = (row['seconds'] or 0) / 3600
        display = await ui.resolve_user_display(interaction.client, row['user_id'], interaction.guild)
        lines.append(f'{display}: {format_time(hours)} → ${hours * wage:,.2f}')

    await interaction.response.send_message('\n'.join(lines), allowed_mentions=ui.MENTIONS_ONLY_USERS)


def register(tree: app_commands.CommandTree) -> None:
    tree.add_command(group)
