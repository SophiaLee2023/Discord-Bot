"""Activity lookup shared by the tracking and session commands."""

from __future__ import annotations

import sqlite3

import discord
from discord import app_commands

from utils import db
from utils.icons import ICON_COLUMNS

SELECT_COLUMNS = f'id, name, {ICON_COLUMNS}'


def find_by_name(conn: sqlite3.Connection, name: str) -> sqlite3.Row | None:
    return conn.execute(
        f'SELECT {SELECT_COLUMNS} FROM activities WHERE LOWER(name) = LOWER(?)', (name,)
    ).fetchone()


NO_DEFAULT_MESSAGE = (
    'No activity given and this server has no default activity. '
    'Name an activity, or ask an admin to set one with `/activity default <name>`.'
)


class ActivityUnavailable(Exception):
    """The named activity does not exist, or none was given and no default is set."""


def resolve(conn: sqlite3.Connection, name: str | None, guild: discord.Guild | None) -> sqlite3.Row:
    """Return the activity to use: the named one, otherwise the guild default.

    Every command that takes an optional activity goes through here, so the
    fallback rule and its error messages are defined in one place.
    """
    if name:
        row = find_by_name(conn, name)
        if row is None:
            raise ActivityUnavailable(f'Activity **{name}** not found!')
        return row

    default_id = db.get_default_activity_id(guild.id) if guild is not None else None
    if default_id is not None:
        row = conn.execute(f'SELECT {SELECT_COLUMNS} FROM activities WHERE id = ?', (default_id,)).fetchone()
        if row is not None:
            return row

    raise ActivityUnavailable(NO_DEFAULT_MESSAGE)


async def autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    rows = db.query_all('SELECT name FROM activities ORDER BY name')
    needle = current.lower()
    return [
        app_commands.Choice(name=row['name'], value=row['name'])
        for row in rows
        if needle in row['name'].lower()
    ][:25]
