"""Activity icon storage.

Icon bytes live in the database rather than on disk, so replacing the source
tree (or a Discord CDN link expiring) never loses an icon.
"""

from __future__ import annotations

import io
import os
import sqlite3

import discord

from utils import db

MAX_ICON_BYTES = 8 * 1024 * 1024
ALLOWED_EXTENSIONS = ('.png', '.jpg', '.jpeg', '.gif', '.webp')

ICON_COLUMNS = 'icon_blob, icon_filename, icon_data'


def safe_filename(activity_id: int, original: str) -> str:
    """Build a stable attachment name, defaulting to .png for odd uploads."""
    extension = os.path.splitext(original or '')[1].lower()
    if extension not in ALLOWED_EXTENSIONS:
        extension = '.png'
    return f'icon_{activity_id}{extension}'


def store(activity_id: int, filename: str, payload: bytes) -> None:
    """Persist icon bytes for an activity, replacing whatever was there."""
    db.execute(
        'UPDATE activities SET icon_blob = ?, icon_filename = ?, icon_data = NULL WHERE id = ?',
        (payload, safe_filename(activity_id, filename), activity_id),
    )


def clear(activity_id: int) -> None:
    db.execute(
        'UPDATE activities SET icon_blob = NULL, icon_filename = NULL, icon_data = NULL WHERE id = ?',
        (activity_id,),
    )


def _column(row: sqlite3.Row | None, name: str):
    if row is None:
        return None
    try:
        return row[name]
    except (IndexError, KeyError):
        return None


def attachment(row: sqlite3.Row | None) -> tuple[discord.File | None, str | None]:
    """Return (file, image_url) for an activity row so an embed can show its icon.

    A fresh `discord.File` is built per call because a file object can only be
    sent once.
    """
    payload = _column(row, 'icon_blob')
    if payload:
        filename = _column(row, 'icon_filename') or safe_filename(_column(row, 'id') or 0, '')
        return discord.File(io.BytesIO(payload), filename=filename), f'attachment://{filename}'

    legacy = _column(row, 'icon_data')
    if legacy and legacy.startswith(('http://', 'https://')):
        return None, legacy
    return None, None


def apply_to_embed(embed: discord.Embed, row: sqlite3.Row | None) -> discord.File | None:
    """Attach an activity's icon to `embed`, returning the file to send (if any)."""
    file_obj, url = attachment(row)
    if url:
        embed.set_image(url=url)
    return file_obj
