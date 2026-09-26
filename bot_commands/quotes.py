"""/quote, /gnaij, /vouch, and /say.

These post as plain channel messages rather than command replies, so the
bot appears to be talking on its own instead of answering someone.
"""

from __future__ import annotations

import random

import discord
from discord import app_commands

from utils import db
from utils import discord_utils as ui
from utils import permissions

MAX_LISTED_QUOTES = 200

# /vouch draws from this fixed pool. It is deliberately not stored in the
# database and has no add or remove command, so it cannot be edited in Discord.
VOUCHES = (
    'TRUE',
    'truthsplosion',
    'so true',
    'exactly',
    'i agree',
    'yes',
    'thats what all americans need to hear',
)

group = app_commands.Group(name='quote', description='Manage quotes')


def random_quote() -> str | None:
    row = db.query_one('SELECT text FROM quotes ORDER BY RANDOM() LIMIT 1')
    return row['text'] if row else None


@group.command(name='add', description='Add a new quote')
@app_commands.describe(text='The quote text to add')
async def quote_add(interaction: discord.Interaction, text: str) -> None:
    text = text.strip()
    if not text:
        await ui.fail(interaction, 'A quote cannot be empty.')
        return

    db.execute('INSERT INTO quotes (text) VALUES (?)', (text,))
    await ui.send_standalone(interaction, embed=ui.notice('Quote added.', ui.GREEN))


@group.command(name='remove', description='Remove a quote by id')
@app_commands.describe(id='Quote id to remove')
async def quote_remove(interaction: discord.Interaction, id: int) -> None:
    if not permissions.is_admin(interaction):
        await ui.fail(interaction, 'Only admins can remove quotes.')
        return

    if not db.execute('DELETE FROM quotes WHERE id = ?', (id,)):
        await ui.fail(interaction, 'Quote not found.')
        return
    await ui.send_standalone(interaction, embed=ui.notice('Quote removed.', ui.GREEN))


@group.command(name='list', description='List all quotes')
async def quote_list(interaction: discord.Interaction) -> None:
    rows = db.query_all('SELECT id, text FROM quotes ORDER BY id')
    if not rows:
        await ui.send_standalone(interaction, embed=ui.notice('No quotes found.', ui.GREY))
        return

    lines = [f'{row["id"]}. {row["text"]}' for row in rows[:MAX_LISTED_QUOTES]]
    embed = discord.Embed(title='Quotes', description='\n'.join(lines)[:4096], color=ui.BLURPLE)
    if len(rows) > MAX_LISTED_QUOTES:
        embed.set_footer(text=f'Showing {MAX_LISTED_QUOTES} of {len(rows)} quotes.')
    await ui.send_standalone(interaction, embed=embed)


@app_commands.command(name='gnaij', description='Say a random quote')
async def gnaij(interaction: discord.Interaction) -> None:
    quote = random_quote()
    if quote is None:
        await ui.fail(interaction, 'No quotes available.')
        return
    await ui.send_standalone(interaction, content=quote)


@app_commands.command(name='vouch', description='Vouch for whatever was just said')
async def vouch(interaction: discord.Interaction) -> None:
    await ui.send_standalone(interaction, content=random.choice(VOUCHES))


@app_commands.command(name='say', description='Make the bot say something')
@app_commands.describe(message='What the bot should say')
async def say(interaction: discord.Interaction, message: str) -> None:
    await ui.send_standalone(interaction, content=message)


def register(tree: app_commands.CommandTree) -> None:
    tree.add_command(group)
    tree.add_command(gnaij)
    tree.add_command(vouch)
    tree.add_command(say)
