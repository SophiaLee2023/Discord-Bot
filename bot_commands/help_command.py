"""/commands — the in-Discord command reference."""

from __future__ import annotations

import discord
from discord import app_commands

from utils import discord_utils as ui

SECTIONS: tuple[tuple[str, tuple[tuple[str, str], ...]], ...] = (
    ('Activity Management', (
        ('/activity add <name>', 'Add a new activity (admin only)'),
        ('/activity remove <name>', 'Remove an activity (admin only)'),
        ('/activity list', 'List all activities'),
        ('/activity icon <name> <image>', 'Set an icon for an activity (admin only)'),
        ('/activity default [name]', 'View or set this server default activity (admin only to set)'),
        ('/activity if <wage> [activity]', 'Estimate earnings for an activity'),
    )),
    ('Time Tracking', (
        ('/clockin [activity]', 'Clock in to an activity, with clock out/pause/resume buttons'),
        ('/clockout', 'Clock out, with edit/note buttons on the summary'),
        ('/pause', 'Pause your current clock'),
        ('/resume', 'Resume your paused clock'),
        ('/cancel', 'Cancel and delete your active session'),
    )),
    ('Statistics', (
        ('/stats [user]', 'View stats with 12-week activity and metrics'),
        ('/leaderboard [activity]', 'View activity leaderboard'),
    )),
    ('Sessions', (
        ('/session add <duration> [date] [activity]', 'Add a session, e.g. `2h 15m`, `2h30m`, `2:15:00`'),
        ('/session list', 'List your sessions'),
        ('/session edit <id> [date] [duration]', 'Change a session date and/or duration'),
        ('/session remove <id>', 'Remove one of your own sessions by id'),
        ('/session combine <ids>', 'Combine matching sessions, e.g. `11, 12, 13`'),
        ('/session tag <note> <id>', 'Attach a note to a session by id'),
    )),
    ('Preferences', (
        ('/timezone [timezone]', 'View or set the timezone used for your daily stats'),
    )),
    ('Fun', (
        ('/quote add <text>', 'Add a new quote'),
        ('/quote remove <id>', 'Remove a quote (admin only)'),
        ('/quote list', 'List all quotes'),
        ('/gnaij', 'Say a random quote; saying "gnaij" in chat does the same'),
        ('/vouch', 'Vouch for whatever was just said'),
        ('/say <message>', 'Make the bot repeat a message'),
        ('/impersonate <user>', "Copy a member's avatar, nickname and colour onto the bot (admin only)"),
    )),
    ('Server Setup (server admins)', (
        ('/admin add|remove <role>', 'Grant or revoke bot admin privileges for a role'),
        ('/admin list', 'List all admin roles'),
        ('/channel add|remove <channel>', 'Restrict the bot to specific channels'),
        ('/channel list', 'List allowed channels'),
    )),
    ('Help', (
        ('/commands', 'Show this list'),
    )),
)


@app_commands.command(name='commands', description='Show all available commands')
async def commands_help(interaction: discord.Interaction) -> None:
    embed = discord.Embed(
        title='Time Tracking Bot Commands',
        description='`<required>` `[optional]`. An omitted activity falls back to the server default.',
        color=ui.BLURPLE,
    )
    for title, entries in SECTIONS:
        embed.add_field(
            name=title,
            value='\n'.join(f'`{command}` — {description}' for command, description in entries),
            inline=False,
        )
    await interaction.response.send_message(embed=embed)


def register(tree: app_commands.CommandTree) -> None:
    tree.add_command(commands_help)
