"""Admin-role and channel-restriction checks.

Both are consulted on nearly every interaction, so the guild rows are cached in
memory and invalidated whenever a command writes to them.
"""

from __future__ import annotations

import discord

from utils import db

_admin_roles: dict[int, frozenset[int]] = {}
_allowed_channels: dict[int, frozenset[int]] = {}


def _cached(cache: dict[int, frozenset[int]], guild_id: int, sql: str, column: str) -> frozenset[int]:
    if guild_id not in cache:
        rows = db.query_all(sql, (guild_id,))
        cache[guild_id] = frozenset(row[column] for row in rows)
    return cache[guild_id]


def admin_role_ids(guild_id: int) -> frozenset[int]:
    return _cached(_admin_roles, guild_id, 'SELECT role_id FROM admin_roles WHERE guild_id = ?', 'role_id')


def allowed_channel_ids(guild_id: int) -> frozenset[int]:
    return _cached(
        _allowed_channels, guild_id, 'SELECT channel_id FROM allowed_channels WHERE guild_id = ?', 'channel_id'
    )


def invalidate(guild_id: int) -> None:
    """Drop cached rows for a guild after an admin-role or channel change."""
    _admin_roles.pop(guild_id, None)
    _allowed_channels.pop(guild_id, None)


def _guild_permissions(interaction: discord.Interaction) -> discord.Permissions | None:
    """The invoker's guild permissions, or None outside a guild."""
    if interaction.guild is None:
        return None
    return getattr(interaction.user, 'guild_permissions', None)


def is_admin(interaction: discord.Interaction) -> bool:
    """True if the invoking member manages the guild or holds a configured admin role."""
    perms = _guild_permissions(interaction)
    if perms is None:
        return False
    if perms.manage_guild:
        return True
    role_ids = admin_role_ids(interaction.guild.id)
    return any(role.id in role_ids for role in getattr(interaction.user, 'roles', ()))


def is_server_admin(interaction: discord.Interaction) -> bool:
    """True only for members with the Discord Administrator permission."""
    perms = _guild_permissions(interaction)
    return perms is not None and perms.administrator


def channel_allowed(interaction: discord.Interaction) -> bool:
    """True if the bot may respond here. No configured channels means anywhere."""
    if interaction.guild is None or interaction.channel is None:
        return True
    allowed = allowed_channel_ids(interaction.guild.id)
    if not allowed:
        return True
    channel = interaction.channel
    # Threads inherit their parent channel's permission.
    return channel.id in allowed or getattr(channel, 'parent_id', None) in allowed


def channel_allowed_for_message(message: discord.Message) -> bool:
    if message.guild is None:
        return True
    allowed = allowed_channel_ids(message.guild.id)
    if not allowed:
        return True
    return message.channel.id in allowed or getattr(message.channel, 'parent_id', None) in allowed
