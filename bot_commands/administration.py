"""/admin and /channel — configure which roles may administer the bot and where it responds."""

from __future__ import annotations

import discord
from discord import app_commands

from utils import db
from utils import discord_utils as ui
from utils import permissions

admin_group = app_commands.Group(name='admin', description='Manage admin roles')
channel_group = app_commands.Group(name='channel', description='Manage the channels the bot responds in')


def _require_server_admin(interaction: discord.Interaction, subject: str) -> str | None:
    if interaction.guild is None:
        return 'This command only works in a server.'
    if not permissions.is_server_admin(interaction):
        return f'Only server admins can manage {subject}!'
    return None


@admin_group.command(name='add', description='Add an admin role')
@app_commands.describe(role='Select a role to add as admin')
async def admin_add(interaction: discord.Interaction, role: discord.Role) -> None:
    if problem := _require_server_admin(interaction, 'admin roles'):
        await ui.fail(interaction, problem)
        return

    changed = db.execute(
        'INSERT OR IGNORE INTO admin_roles (guild_id, role_id) VALUES (?, ?)',
        (interaction.guild.id, role.id),
    )
    if not changed:
        await ui.fail(interaction, f'{role.mention} is already an admin role!')
        return

    permissions.invalidate(interaction.guild.id)
    await ui.send(interaction, f'Added {role.mention} as an admin role!')


@admin_group.command(name='remove', description='Remove an admin role')
@app_commands.describe(role='Select a role to remove from admin')
async def admin_remove(interaction: discord.Interaction, role: discord.Role) -> None:
    if problem := _require_server_admin(interaction, 'admin roles'):
        await ui.fail(interaction, problem)
        return

    removed = db.execute(
        'DELETE FROM admin_roles WHERE guild_id = ? AND role_id = ?', (interaction.guild.id, role.id)
    )
    if not removed:
        await ui.fail(interaction, f'{role.mention} is not an admin role!')
        return

    permissions.invalidate(interaction.guild.id)
    await ui.send(interaction, f'Removed {role.mention} from admin roles!')


@admin_group.command(name='list', description='List all admin roles')
async def admin_list(interaction: discord.Interaction) -> None:
    if interaction.guild is None:
        await ui.fail(interaction, 'This command only works in a server.')
        return

    role_ids = permissions.admin_role_ids(interaction.guild.id)
    if not role_ids:
        await ui.send(interaction, 'No admin roles configured yet!', color=ui.GREY)
        return

    embed = discord.Embed(title='Admin Roles', color=ui.BLURPLE)
    embed.description = '\n'.join(
        role.mention if (role := interaction.guild.get_role(role_id)) else f'*Deleted role ({role_id})*'
        for role_id in sorted(role_ids)
    )
    await interaction.response.send_message(embed=embed)


@channel_group.command(name='add', description='Restrict the bot to a channel')
@app_commands.describe(channel='Channel the bot should respond in')
async def channel_add(interaction: discord.Interaction, channel: discord.TextChannel) -> None:
    if problem := _require_server_admin(interaction, 'bot channels'):
        await ui.fail(interaction, problem)
        return

    changed = db.execute(
        'INSERT OR IGNORE INTO allowed_channels (guild_id, channel_id) VALUES (?, ?)',
        (interaction.guild.id, channel.id),
    )
    if not changed:
        await ui.fail(interaction, f'{channel.mention} is already an allowed channel!')
        return

    permissions.invalidate(interaction.guild.id)
    await ui.send(interaction, f'The bot will now respond in {channel.mention}.')


@channel_group.command(name='remove', description='Remove a channel restriction')
@app_commands.describe(channel='Channel to stop restricting the bot to')
async def channel_remove(interaction: discord.Interaction, channel: discord.TextChannel) -> None:
    if problem := _require_server_admin(interaction, 'bot channels'):
        await ui.fail(interaction, problem)
        return

    removed = db.execute(
        'DELETE FROM allowed_channels WHERE guild_id = ? AND channel_id = ?',
        (interaction.guild.id, channel.id),
    )
    if not removed:
        await ui.fail(interaction, f'{channel.mention} is not an allowed channel!')
        return

    permissions.invalidate(interaction.guild.id)
    remaining = permissions.allowed_channel_ids(interaction.guild.id)
    suffix = '' if remaining else ' The bot now responds everywhere.'
    await ui.send(interaction, f'Removed {channel.mention} from the allowed channels.{suffix}')


@channel_group.command(name='list', description='List the channels the bot responds in')
async def channel_list(interaction: discord.Interaction) -> None:
    if interaction.guild is None:
        await ui.fail(interaction, 'This command only works in a server.')
        return

    channel_ids = permissions.allowed_channel_ids(interaction.guild.id)
    if not channel_ids:
        await ui.send(
            interaction, 'No channel restrictions set — the bot responds everywhere.', color=ui.GREY
        )
        return

    embed = discord.Embed(title='Allowed Channels', color=ui.BLURPLE)
    embed.description = '\n'.join(
        channel.mention if (channel := interaction.guild.get_channel(channel_id)) else f'*Deleted channel ({channel_id})*'
        for channel_id in sorted(channel_ids)
    )
    await interaction.response.send_message(embed=embed)


def register(tree: app_commands.CommandTree) -> None:
    tree.add_command(admin_group)
    tree.add_command(channel_group)
