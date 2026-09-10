"""/timezone — view or set the timezone used for day boundaries and streaks."""

from __future__ import annotations

from datetime import datetime

import discord
from discord import app_commands

from utils import db
from utils import discord_utils as ui
from utils import permissions
from utils import timezones


async def timezone_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    """Preview matching zones with their current offset and local time."""
    now = datetime.now()
    return [
        app_commands.Choice(name=timezones.preview(zone, now)[:100], value=zone)
        for zone in timezones.search(current)
    ]


@app_commands.command(name='timezone', description='View or set the timezone used for your daily stats')
@app_commands.describe(
    user='Optional: another member to view or set (admin only)',
    timezone='Start typing to preview timezones, e.g. Los_Angeles or Europe/',
)
@app_commands.autocomplete(timezone=timezone_autocomplete)
async def timezone_command(
    interaction: discord.Interaction,
    user: discord.User | None = None,
    timezone: str | None = None,
) -> None:
    target = user or interaction.user

    if user is not None and user.id != interaction.user.id and not permissions.is_admin(interaction):
        await ui.fail(interaction, 'Only admins can view or set the timezone of another member.')
        return

    if timezone is None:
        await _show(interaction, target)
        return

    zone_name = timezones.canonical_name(timezone)
    if zone_name is None:
        await ui.fail(
            interaction,
            f'**{timezone}** is not a known timezone. Pick one from the suggestions, '
            'or use an IANA name such as `America/Los_Angeles`.',
        )
        return

    db.set_user_timezone(target.id, zone_name)
    whose = 'Your' if target.id == interaction.user.id else f"{target.mention}'s"
    await ui.send(
        interaction,
        f'{whose} timezone is now **{zone_name}** ({timezones.preview(zone_name)}).',
        ephemeral=True,
    )


async def _show(interaction: discord.Interaction, target: discord.abc.User) -> None:
    zone_name = db.get_user_timezone(target.id)
    whose = 'Your' if target.id == interaction.user.id else f"{target.mention}'s"

    if not zone_name:
        await ui.send(
            interaction,
            f'{whose} timezone is not set, so server time is used. '
            'Set one with `/timezone timezone:<zone>`.',
            color=ui.GREY,
            ephemeral=True,
        )
        return

    await ui.send(
        interaction,
        f'{whose} timezone is **{zone_name}** ({timezones.preview(zone_name)}).',
        color=ui.BLURPLE,
        ephemeral=True,
    )


def register(tree: app_commands.CommandTree) -> None:
    tree.add_command(timezone_command)
