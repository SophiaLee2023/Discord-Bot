"""Discord time-tracking bot: startup, global checks, and event handlers.

Command implementations live in `bot_commands/`; shared helpers live in `utils/`.
"""

from __future__ import annotations

import logging
import os
import re

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

import bot_commands
from utils import db
from utils import discord_utils as ui
from utils import permissions
from utils.db import init_db

load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')

GNAIJ_PATTERN = re.compile(r'\bgnaij\b', re.IGNORECASE)

log = logging.getLogger('timetracker')


def build_bot() -> commands.Bot:
    intents = discord.Intents.default()
    intents.message_content = True
    intents.members = True

    instance = commands.Bot(
        command_prefix='.',
        intents=intents,
        help_command=None,
        allowed_mentions=ui.MENTIONS_ONLY_USERS,
    )
    instance.tree.interaction_check = _channel_check
    instance.tree.error(_on_app_command_error)
    return instance


async def _channel_check(interaction: discord.Interaction) -> bool:
    """Reject interactions outside the configured channels, with a visible reason.

    Setup commands stay reachable everywhere so an admin cannot lock themselves out.
    """
    command = interaction.command
    root_name = command.qualified_name.split(' ')[0] if command else None
    if root_name in bot_commands.CHANNEL_CHECK_EXEMPT or permissions.channel_allowed(interaction):
        return True

    if interaction.type is discord.InteractionType.application_command:
        await ui.fail(interaction, 'This bot is not enabled in this channel.')
    return False


async def _on_app_command_error(
    interaction: discord.Interaction, error: app_commands.AppCommandError
) -> None:
    """Report command failures to the user instead of leaving the interaction hanging."""
    if isinstance(error, app_commands.CheckFailure):
        return

    log.exception('Command %s failed', interaction.command and interaction.command.qualified_name, exc_info=error)
    cause = getattr(error, 'original', error)
    try:
        await ui.fail(interaction, f'Something went wrong: {cause}')
    except discord.HTTPException:
        pass


bot = build_bot()


@bot.event
async def on_ready() -> None:
    log.info('%s connected to Discord', bot.user)
    try:
        synced = await bot.tree.sync()
        log.info('Synced %d global command(s)', len(synced))
    except discord.HTTPException:
        log.exception('Failed to sync commands')

    await bot.change_presence(activity=discord.Game(name='/gnaij'))
    log.info('Bot is ready.')


@bot.event
async def on_message(message: discord.Message) -> None:
    """Reply with a random quote whenever someone says 'gnaij'."""
    if not message.author.bot and GNAIJ_PATTERN.search(message.content):
        if permissions.channel_allowed_for_message(message):
            quote = bot_commands.quotes.random_quote()
            await message.channel.send(quote or 'No quotes available.')

    await bot.process_commands(message)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s: %(message)s')
    if not TOKEN:
        raise SystemExit('DISCORD_TOKEN is not set. Add it to your .env file.')

    init_db()
    log.info('Using database %s', db.DB_PATH)
    bot_commands.register_all(bot.tree)
    bot.run(TOKEN, log_handler=None)


if __name__ == '__main__':
    main()
