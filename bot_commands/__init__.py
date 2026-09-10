"""Slash command definitions, grouped by feature area."""

from discord import app_commands

from bot_commands import (
    activity_commands,
    administration,
    help_command,
    impersonate,
    quotes,
    session_commands,
    statistics,
    tracking,
    user_timezone,
)

MODULES = (
    activity_commands,
    tracking,
    statistics,
    session_commands,
    user_timezone,
    administration,
    impersonate,
    quotes,
    help_command,
)

# Commands that must stay reachable even when the bot is restricted to channels
# the invoker cannot use, so a server admin can never lock themselves out.
CHANNEL_CHECK_EXEMPT = frozenset({'channel', 'admin', 'commands'})


def register_all(tree: app_commands.CommandTree) -> None:
    for module in MODULES:
        module.register(tree)
