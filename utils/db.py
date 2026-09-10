"""SQLite access: connection handling, schema management, and settings accessors."""

from __future__ import annotations

import glob
import os
import sqlite3
from contextlib import contextmanager
from collections.abc import Iterator

DEFAULT_DB_NAME = 'bot_data.db'

# Earlier releases named the file after time tracking, but it now also holds
# quotes, admin roles, allowed channels, timezones, and the bot's own identity.
# Older names are still opened so an existing deployment keeps working after an
# update without anyone having to rename anything.
LEGACY_DB_NAMES = ('time_tracker.db',)


def _find_db_path(preferred: str = DEFAULT_DB_NAME) -> str:
    """Return the database file to use, tolerating older file names."""
    for candidate in (preferred, *LEGACY_DB_NAMES):
        if os.path.exists(candidate):
            return candidate
    others = sorted(glob.glob('*.db'))
    return others[0] if others else preferred


DB_PATH = os.getenv('BOT_DB') or os.getenv('TIME_TRACKER_DB') or _find_db_path()


@contextmanager
def db(commit: bool = False) -> Iterator[sqlite3.Connection]:
    """Yield a row-factory connection, always closing it and optionally committing.

    Using this instead of manual open/close means an early return or an exception
    can no longer leak a connection.
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        if commit:
            conn.commit()
    finally:
        conn.close()


def query_all(sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    with db() as conn:
        return conn.execute(sql, params).fetchall()


def query_one(sql: str, params: tuple = ()) -> sqlite3.Row | None:
    with db() as conn:
        return conn.execute(sql, params).fetchone()


def execute(sql: str, params: tuple = ()) -> int:
    """Run a write statement and return the number of affected rows."""
    with db(commit=True) as conn:
        return conn.execute(sql, params).rowcount


# --------------------------------------------------------------------------- #
# Schema
# --------------------------------------------------------------------------- #

TABLES = (
    '''CREATE TABLE IF NOT EXISTS activities (
        id INTEGER PRIMARY KEY,
        name TEXT UNIQUE NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        icon_data TEXT,
        icon_blob BLOB,
        icon_filename TEXT
    )''',
    '''CREATE TABLE IF NOT EXISTS sessions (
        id INTEGER PRIMARY KEY,
        user_id INTEGER NOT NULL,
        activity_id INTEGER NOT NULL,
        date TEXT NOT NULL,
        duration_seconds INTEGER NOT NULL,
        clock_in TIMESTAMP,
        clock_out TIMESTAMP,
        paused_at TIMESTAMP,
        note TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (activity_id) REFERENCES activities (id)
    )''',
    '''CREATE TABLE IF NOT EXISTS admin_roles (
        id INTEGER PRIMARY KEY,
        guild_id INTEGER NOT NULL,
        role_id INTEGER NOT NULL,
        UNIQUE(guild_id, role_id)
    )''',
    '''CREATE TABLE IF NOT EXISTS allowed_channels (
        id INTEGER PRIMARY KEY,
        guild_id INTEGER NOT NULL,
        channel_id INTEGER NOT NULL,
        UNIQUE(guild_id, channel_id)
    )''',
    '''CREATE TABLE IF NOT EXISTS guild_settings (
        id INTEGER PRIMARY KEY,
        guild_id INTEGER UNIQUE NOT NULL,
        default_activity_id INTEGER,
        FOREIGN KEY (default_activity_id) REFERENCES activities (id)
    )''',
    '''CREATE TABLE IF NOT EXISTS user_settings (
        user_id INTEGER PRIMARY KEY,
        timezone TEXT
    )''',
    '''CREATE TABLE IF NOT EXISTS bot_avatar (
        id INTEGER PRIMARY KEY,
        source_user_id INTEGER NOT NULL,
        source_key TEXT,
        applied_key TEXT
    )''',
    '''CREATE TABLE IF NOT EXISTS bot_colour_role (
        guild_id INTEGER PRIMARY KEY,
        role_id INTEGER NOT NULL
    )''',
    '''CREATE TABLE IF NOT EXISTS quotes (
        id INTEGER PRIMARY KEY,
        text TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''',
)

INDEXES = (
    'CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions (user_id)',
    'CREATE INDEX IF NOT EXISTS idx_sessions_activity ON sessions (activity_id)',
    'CREATE INDEX IF NOT EXISTS idx_sessions_open ON sessions (user_id, clock_out)',
)

# Columns added after the original schema shipped, applied to existing databases.
# Tables an earlier version created that are no longer used. They are dropped on
# startup so nothing lingers in the database — bot_identity held avatar images.
REMOVED_TABLES = ('bot_identity', 'impersonation_roles')

ADDED_COLUMNS = {
    'activities': (
        ('icon_data', 'TEXT'),
        ('icon_blob', 'BLOB'),
        ('icon_filename', 'TEXT'),
    ),
    'sessions': (
        ('clock_in', 'TIMESTAMP'),
        ('clock_out', 'TIMESTAMP'),
        ('paused_at', 'TIMESTAMP'),
        ('note', 'TEXT'),
    ),
    'user_settings': (
        ('timezone', 'TEXT'),
    ),
}


def init_db() -> None:
    """Create missing tables, columns, and indexes. Safe to run on every start."""
    with db(commit=True) as conn:
        for statement in TABLES:
            conn.execute(statement)

        # Backfill columns before the indexes that reference them, so a database
        # created by an older version can still be upgraded in place.
        for table, columns in ADDED_COLUMNS.items():
            existing = {row['name'] for row in conn.execute(f'PRAGMA table_info({table})')}
            for column, column_type in columns:
                if column not in existing:
                    conn.execute(f'ALTER TABLE {table} ADD COLUMN {column} {column_type}')

        for statement in INDEXES:
            conn.execute(statement)

        _migrate_icons_into_db(conn)
        dropped = _drop_removed_tables(conn)

    if dropped:
        # DROP only frees pages for reuse; VACUUM rewrites the file so the
        # discarded image bytes are actually gone from disk.
        with db() as conn:
            conn.execute('VACUUM')


def _drop_removed_tables(conn: sqlite3.Connection) -> bool:
    """Drop tables from retired features. Returns True if anything was dropped."""
    present = {row['name'] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    dropped = False
    for table in REMOVED_TABLES:
        if table in present:
            conn.execute(f'DROP TABLE {table}')
            dropped = True
    return dropped


def _migrate_icons_into_db(conn: sqlite3.Connection) -> None:
    """Pull icons that were saved as files on disk into the database itself.

    Icon files used to live in ./activity_icons, so replacing the source tree
    lost them. Storing the bytes alongside the rest of the data keeps icons
    independent of the code.
    """
    from utils.icons import safe_filename  # imported here: icons depends on this module

    rows = conn.execute(
        'SELECT id, icon_data FROM activities WHERE icon_blob IS NULL AND icon_data IS NOT NULL'
    ).fetchall()
    for row in rows:
        path = row['icon_data']
        if path.startswith(('http://', 'https://')) or not os.path.isfile(path):
            continue
        try:
            with open(path, 'rb') as handle:
                payload = handle.read()
        except OSError:
            continue
        conn.execute(
            'UPDATE activities SET icon_blob = ?, icon_filename = ?, icon_data = NULL WHERE id = ?',
            (payload, safe_filename(row['id'], os.path.basename(path)), row['id']),
        )


# --------------------------------------------------------------------------- #
# Settings accessors
# --------------------------------------------------------------------------- #

def get_default_activity_id(guild_id: int) -> int | None:
    row = query_one('SELECT default_activity_id FROM guild_settings WHERE guild_id = ?', (guild_id,))
    return row['default_activity_id'] if row else None


def set_default_activity_id(guild_id: int, activity_id: int) -> None:
    execute(
        'INSERT INTO guild_settings (guild_id, default_activity_id) VALUES (?, ?) '
        'ON CONFLICT(guild_id) DO UPDATE SET default_activity_id = excluded.default_activity_id',
        (guild_id, activity_id),
    )


def get_bot_avatar_state() -> sqlite3.Row | None:
    """Whose avatar the bot is wearing, and the hashes it was derived from.

    The bot's avatar is account-wide, so there is a single row.
    """
    return query_one('SELECT source_user_id, source_key, applied_key FROM bot_avatar WHERE id = 1')


def set_bot_avatar_state(source_user_id: int, source_key: str | None, applied_key: str | None) -> None:
    execute(
        'INSERT INTO bot_avatar (id, source_user_id, source_key, applied_key) VALUES (1, ?, ?, ?) '
        'ON CONFLICT(id) DO UPDATE SET source_user_id = excluded.source_user_id, '
        'source_key = excluded.source_key, applied_key = excluded.applied_key',
        (source_user_id, source_key, applied_key),
    )


def get_colour_role_id(guild_id: int) -> int | None:
    """The role /impersonate created in this guild to carry the bot's colour."""
    row = query_one('SELECT role_id FROM bot_colour_role WHERE guild_id = ?', (guild_id,))
    return row['role_id'] if row else None


def set_colour_role_id(guild_id: int, role_id: int) -> None:
    execute(
        'INSERT INTO bot_colour_role (guild_id, role_id) VALUES (?, ?) '
        'ON CONFLICT(guild_id) DO UPDATE SET role_id = excluded.role_id',
        (guild_id, role_id),
    )


def clear_colour_role_id(guild_id: int) -> None:
    execute('DELETE FROM bot_colour_role WHERE guild_id = ?', (guild_id,))


def get_user_timezone(user_id: int) -> str | None:
    row = query_one('SELECT timezone FROM user_settings WHERE user_id = ?', (user_id,))
    return row['timezone'] if row else None


def set_user_timezone(user_id: int, timezone_name: str) -> None:
    execute(
        'INSERT INTO user_settings (user_id, timezone) VALUES (?, ?) '
        'ON CONFLICT(user_id) DO UPDATE SET timezone = excluded.timezone',
        (user_id, timezone_name),
    )
