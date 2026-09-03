import os
import glob
import sqlite3
from datetime import datetime, timedelta, date as _date
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from typing import List
from typing import Optional
from dotenv import load_dotenv
import discord
import re
from discord.ext import commands
from discord import app_commands
from session_utils import build_session_list_fields, parse_session_ids, session_duration_seconds_sql
from time_utils import format_time, parse_date_input, parse_hms_to_seconds

load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix='.', intents=intents, help_command=None)

# Ensure mentions in bot responses render as clickable where appropriate
try:
    bot.allowed_mentions = discord.AllowedMentions(users=True, roles=False, everyone=False)
except Exception:
    # older discord.py versions may not accept direct assignment; ignore if unavailable
    pass


def find_db_path(preferred='time_tracker.db'):
    # Prefer explicit file if it exists
    if os.path.exists(preferred):
        return preferred

    # Fallback: accept any legacy .db file in cwd
    dbs = glob.glob('*.db')
    if dbs:
        # pick the first one found (legacy compatibility)
        return dbs[0]

    # default
    return preferred


DB_PATH = find_db_path()


def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS activities (
            id INTEGER PRIMARY KEY,
            name TEXT UNIQUE NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            icon_data TEXT
        )
    ''')

    # Legacy `time_entries` table removed; sessions table is used instead.

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS admin_roles (
            id INTEGER PRIMARY KEY,
            guild_id INTEGER NOT NULL,
            role_id INTEGER NOT NULL,
            UNIQUE(guild_id, role_id)
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS allowed_channels (
            id INTEGER PRIMARY KEY,
            guild_id INTEGER NOT NULL,
            channel_id INTEGER NOT NULL,
            UNIQUE(guild_id, channel_id)
        )
    ''')

    # Guild settings (store default activity id etc.)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS guild_settings (
            id INTEGER PRIMARY KEY,
            guild_id INTEGER UNIQUE NOT NULL,
            default_activity_id INTEGER,
            FOREIGN KEY (default_activity_id) REFERENCES activities (id)
        )
    ''')

    # Sessions table: store per-day sessions (date only) with duration in seconds
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS sessions (
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
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS quotes (
            id INTEGER PRIMARY KEY,
            text TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    conn.commit()
    conn.close()

def get_db():
    # Always connect to DB_PATH (which may have been set to a legacy .db file)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def sync_db_schema(conn=None):
    """Ensure expected tables/columns exist and migrate common legacy types.
    This is safe to run multiple times.
    """
    close_conn = False
    if conn is None:
        conn = get_db()
        close_conn = True
    cursor = conn.cursor()

    # Ensure activities.icon_data exists
    cursor.execute("PRAGMA table_info(activities)")
    cols = [r[1] for r in cursor.fetchall()]
    if 'icon_data' not in cols:
        try:
            cursor.execute('ALTER TABLE activities ADD COLUMN icon_data TEXT')
        except Exception:
            pass

    # Ensure guild_settings exists (created in init_db normally)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS guild_settings (
            id INTEGER PRIMARY KEY,
            guild_id INTEGER UNIQUE NOT NULL,
            default_activity_id INTEGER,
            FOREIGN KEY (default_activity_id) REFERENCES activities (id)
        )
    ''')

    # Normalize timestamp columns: if clock_in/clock_out are integers (epoch), convert to ISO strings
    # If a legacy `time_entries` table exists, normalize integer epoch timestamps to ISO strings
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='time_entries'")
    if cursor.fetchone():
        cursor.execute("PRAGMA table_info(time_entries)")
        te_cols = cursor.fetchall()
        # attempt to detect type of clock_in column
        col_info = {r[1]: r[2].upper() for r in te_cols}
        if col_info.get('clock_in', '') in ('INTEGER', 'INT') or col_info.get('clock_out', '') in ('INTEGER', 'INT'):
            # read all rows and convert epoch ints to ISO strings
            cursor.execute('SELECT id, clock_in, clock_out FROM time_entries')
            rows = cursor.fetchall()
            for row in rows:
                id_ = row['id']
                ci = row['clock_in']
                co = row['clock_out']
                updates = {}
                try:
                    if ci is not None and isinstance(ci, int):
                        updates['clock_in'] = datetime.fromtimestamp(ci).isoformat()
                    elif ci is not None and isinstance(ci, str) and ci.isdigit():
                        updates['clock_in'] = datetime.fromtimestamp(int(ci)).isoformat()
                except Exception:
                    pass
                try:
                    if co is not None and isinstance(co, int):
                        updates['clock_out'] = datetime.fromtimestamp(co).isoformat()
                    elif co is not None and isinstance(co, str) and co.isdigit():
                        updates['clock_out'] = datetime.fromtimestamp(int(co)).isoformat()
                except Exception:
                    pass

                if updates:
                    set_clause = ', '.join([f"{k} = ?" for k in updates.keys()])
                    params = list(updates.values()) + [id_]
                    try:
                        cursor.execute(f'UPDATE time_entries SET {set_clause} WHERE id = ?', params)
                    except Exception:
                        pass

    # Ensure sessions has clock_in/clock_out columns for open sessions support
    cursor.execute("PRAGMA table_info(sessions)")
    sess_cols = [r[1] for r in cursor.fetchall()]
    if 'clock_in' not in sess_cols:
        try:
            cursor.execute('ALTER TABLE sessions ADD COLUMN clock_in TIMESTAMP')
        except Exception:
            pass
    if 'clock_out' not in sess_cols:
        try:
            cursor.execute('ALTER TABLE sessions ADD COLUMN clock_out TIMESTAMP')
        except Exception:
            pass
    if 'paused_at' not in sess_cols:
        try:
            cursor.execute('ALTER TABLE sessions ADD COLUMN paused_at TIMESTAMP')
        except Exception:
            pass
    if 'note' not in sess_cols:
        try:
            cursor.execute('ALTER TABLE sessions ADD COLUMN note TEXT')
        except Exception:
            pass

    # Ensure user_settings table exists (store per-user timezone preferences)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS user_settings (
            user_id INTEGER PRIMARY KEY,
            timezone TEXT
        )
    ''')
    # ensure confirmed column exists for user_settings
    cursor.execute("PRAGMA table_info(user_settings)")
    us_cols = [r[1] for r in cursor.fetchall()]
    if 'confirmed' not in us_cols:
        try:
            cursor.execute('ALTER TABLE user_settings ADD COLUMN confirmed INTEGER DEFAULT 0')
        except Exception:
            pass

    conn.commit()
    if close_conn:
        conn.close()


def migrate_time_entries_to_sessions(conn=None):
    """Migrate completed time_entries into sessions table.
    Adds sessions.time_entry_id column if missing. Skips entries already migrated.
    Returns dict with counts.
    """
    close_conn = False
    if conn is None:
        conn = get_db()
        close_conn = True
    cursor = conn.cursor()

    # Ensure time_entry_id column exists on sessions
    cursor.execute("PRAGMA table_info(sessions)")
    cols = [r[1] for r in cursor.fetchall()]
    if 'time_entry_id' not in cols:
        try:
            cursor.execute('ALTER TABLE sessions ADD COLUMN time_entry_id INTEGER')
            conn.commit()
        except Exception:
            pass

    # Select all entries (completed and open)
    cursor.execute('SELECT id, user_id, activity_id, clock_in, clock_out FROM time_entries')
    rows = cursor.fetchall()

    inserted = 0
    skipped = 0
    already = 0

    for row in rows:
        te_id = row['id']
        # skip if already migrated
        cursor.execute('SELECT 1 FROM sessions WHERE time_entry_id = ?', (te_id,))
        if cursor.fetchone():
            already += 1
            continue

        try:
            ci_raw = row['clock_in']
            co_raw = row['clock_out']
            ci = None
            co = None
            if ci_raw:
                ci = datetime.fromisoformat(ci_raw)
            if co_raw:
                co = datetime.fromisoformat(co_raw)

            if ci and co:
                duration = int(round((co - ci).total_seconds()))
                date_str = ci.date().isoformat()
                cursor.execute('INSERT INTO sessions (user_id, activity_id, date, duration_seconds, time_entry_id, clock_in, clock_out) VALUES (?, ?, ?, ?, ?, ?, ?)',
                               (row['user_id'], row['activity_id'], date_str, duration, te_id, ci.isoformat(), co.isoformat()))
            elif ci and not co:
                # open session: store clock_in, leave clock_out NULL and duration 0
                date_str = ci.date().isoformat()
                cursor.execute('INSERT INTO sessions (user_id, activity_id, date, duration_seconds, time_entry_id, clock_in) VALUES (?, ?, ?, ?, ?, ?)',
                               (row['user_id'], row['activity_id'], date_str, 0, te_id, ci.isoformat()))
            else:
                # unexpected format, skip
                skipped += 1
                continue

            inserted += 1
        except Exception:
            skipped += 1

    conn.commit()

    # After a successful migration, remove the legacy table.
    try:
        cursor.execute('DROP TABLE IF EXISTS time_entries')
        conn.commit()
    finally:
        if close_conn:
            conn.close()

    return {'inserted': inserted, 'skipped': skipped, 'already': already, 'total': len(rows)}


def get_default_activity_for_guild(guild_id: int):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT default_activity_id FROM guild_settings WHERE guild_id = ?', (guild_id,))
    row = cursor.fetchone()
    conn.close()
    return row['default_activity_id'] if row else None


def set_default_activity_for_guild(guild_id: int, activity_id: int):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('INSERT INTO guild_settings (guild_id, default_activity_id) VALUES (?, ?)'
                   ' ON CONFLICT(guild_id) DO UPDATE SET default_activity_id=excluded.default_activity_id', (guild_id, activity_id))
    conn.commit()
    conn.close()


def get_user_timezone(user_id: int) -> str | None:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT timezone FROM user_settings WHERE user_id = ?', (user_id,))
    row = cursor.fetchone()
    conn.close()
    return row['timezone'] if row else None


def set_user_timezone(user_id: int, tz: str):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('INSERT INTO user_settings (user_id, timezone, confirmed) VALUES (?, ?, 1)'
                   ' ON CONFLICT(user_id) DO UPDATE SET timezone=excluded.timezone, confirmed=1', (user_id, tz))
    conn.commit()
    conn.close()


def get_user_timezone_and_confirmed(user_id: int) -> tuple[str | None, bool]:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT timezone, confirmed FROM user_settings WHERE user_id = ?', (user_id,))
    row = cursor.fetchone()
    conn.close()
    if not row:
        return None, False
    return row['timezone'], bool(row['confirmed'])


async def resolve_user_display(user_id: int, guild: discord.Guild | None = None) -> str:
    """Return a mention string for a user id suitable for embeds and messages.
    Prefer guild member mention, then fetched user mention, else fallback to numeric mention.
    """
    try:
        if guild:
            member = guild.get_member(user_id)
            if member:
                return member.mention
        user = await bot.fetch_user(user_id)
        # If the user isn't a member of this guild, return a readable name instead of a raw numeric mention
        return f'{user.name}#{user.discriminator}'
    except Exception:
        # Last-resort fallback: show numeric id without angle brackets to avoid raw unresolved mention display
        return str(user_id)


def _prepare_icon_attachment(icon_data: Optional[str]):
    """Return (file_obj, url) for an activity icon.
    If icon_data is an HTTP URL, return (None, url). If it's a local path, return (discord.File, attachment-url).
    """
    if not icon_data:
        return None, None
    if icon_data.startswith('http://') or icon_data.startswith('https://'):
        return None, icon_data
    # treat as local path
    try:
        if os.path.exists(icon_data):
            fname = os.path.basename(icon_data)
            return discord.File(icon_data, filename=fname), f'attachment://{fname}'
    except Exception:
        pass
    return None, None







def is_admin(ctx):
    if ctx.guild is None:
        return False

    if ctx.author.guild_permissions.manage_guild:
        return True

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT role_id FROM admin_roles WHERE guild_id = ?', (ctx.guild.id,))
    admin_role_ids = [row['role_id'] for row in cursor.fetchall()]
    conn.close()

    if not admin_role_ids:
        return False

    user_role_ids = [role.id for role in ctx.author.roles]
    return any(role_id in user_role_ids for role_id in admin_role_ids)

def is_admin_app(interaction: discord.Interaction):
    if interaction.guild is None:
        return False

    if interaction.user.guild_permissions.manage_guild:
        return True

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT role_id FROM admin_roles WHERE guild_id = ?', (interaction.guild.id,))
    admin_role_ids = [row['role_id'] for row in cursor.fetchall()]
    conn.close()

    if not admin_role_ids:
        return False

    user_role_ids = [role.id for role in interaction.user.roles]
    return any(role_id in user_role_ids for role_id in admin_role_ids)

@bot.event
async def on_ready():
    print(f'{bot.user} has connected to Discord!')
    print(f'Attempting to sync commands...')
    try:
        # Sync to all guilds first (instant update for testing)
        for guild in bot.guilds:
            synced = await bot.tree.sync(guild=guild)
            print(f'Synced {len(synced)} command(s) in {guild.name}')

        # Also sync globally for new servers
        synced = await bot.tree.sync()
        print(f'Also synced globally ({len(synced)} commands)')
    except Exception as e:
        print(f'Failed to sync commands: {type(e).__name__}: {e}')
        import traceback
        traceback.print_exc()
    await bot.change_presence(activity=discord.Game(name='/gnaij'))
    print('Bot is ready.')


@bot.event
async def on_message(message: discord.Message):
    # Ignore bot messages
    if message.author.bot:
        return

    try:
        # Match whole word 'gnaij' (case-insensitive)
        if re.search(r"\bgnaij\b", message.content, flags=re.IGNORECASE):
            # Respect allowed channels similar to check_channel
            if message.guild:
                conn = get_db()
                cursor = conn.cursor()
                cursor.execute('SELECT channel_id FROM allowed_channels WHERE guild_id = ?', (message.guild.id,))
                allowed_channels = [r['channel_id'] for r in cursor.fetchall()]
                conn.close()
                if allowed_channels and message.channel.id not in allowed_channels:
                    await bot.process_commands(message)
                    return

            # Fetch a random quote and post it publicly
            try:
                conn = get_db()
                cursor = conn.cursor()
                cursor.execute('SELECT id, text FROM quotes ORDER BY RANDOM() LIMIT 1')
                row = cursor.fetchone()
                conn.close()
                if not row:
                    await message.channel.send('No quotes available.')
                else:
                    await message.channel.send(row['text'])
            except Exception as e:
                print(f'Error in on_message gnaij handler: {e}')

    finally:
        # Allow other command processing (prefix-based)
        await bot.process_commands(message)

def check_channel(interaction: discord.Interaction) -> bool:
    if not interaction.guild:
        return True

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT channel_id FROM allowed_channels WHERE guild_id = ?', (interaction.guild.id,))
    allowed_channels = [row['channel_id'] for row in cursor.fetchall()]
    conn.close()

    if allowed_channels and interaction.channel.id not in allowed_channels:
        return False
    return True

async def activity_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> List[app_commands.Choice[str]]:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT name FROM activities ORDER BY name')
    activities = [row['name'] for row in cursor.fetchall()]
    conn.close()

    filtered = [a for a in activities if current.lower() in a.lower()]
    return [app_commands.Choice(name=a, value=a) for a in filtered][:25]

activity = app_commands.Group(name='activity', description='Manage activities')

session = app_commands.Group(name='session', description='Manage sessions')

@activity.command(name='add', description='Add a new activity/commitment to track')
@app_commands.check(lambda i: check_channel(i))
async def activity_add(interaction: discord.Interaction, name: str):
    if not is_admin_app(interaction):
        await interaction.response.send_message('You need to be an admin to use this command!', ephemeral=True)
        return

    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('INSERT INTO activities (name) VALUES (?)', (name,))
        conn.commit()
        conn.close()
        embed = discord.Embed(description=f'Activity **{name}** added!', color=discord.Color.green())
        await interaction.response.send_message(embed=embed)
    except sqlite3.IntegrityError:
        embed = discord.Embed(description=f'Activity **{name}** already exists!', color=discord.Color.red())
        await interaction.response.send_message(embed=embed, ephemeral=True)
    except Exception as e:
        embed = discord.Embed(description=f'Error: {str(e)}', color=discord.Color.red())
        await interaction.response.send_message(embed=embed, ephemeral=True)

@activity.command(name='remove', description='Remove an activity')
@app_commands.autocomplete(name=activity_autocomplete)
@app_commands.check(lambda i: check_channel(i))
async def activity_remove(interaction: discord.Interaction, name: str):
    if not is_admin_app(interaction):
        await interaction.response.send_message('You need to be an admin to use this command!', ephemeral=True)
        return

    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('SELECT id, icon_data FROM activities WHERE LOWER(name) = LOWER(?)', (name,))
        activity_row = cursor.fetchone()

        if not activity_row:
            embed = discord.Embed(description=f'Activity **{name}** not found!', color=discord.Color.red())
            await interaction.response.send_message(embed=embed, ephemeral=True)
            conn.close()
            return

        # delete stored icon file if present
        try:
            old_icon = activity_row.get('icon_data') if 'icon_data' in activity_row.keys() else None
            if old_icon and not (old_icon.startswith('http://') or old_icon.startswith('https://')):
                try:
                    if os.path.exists(old_icon):
                        os.remove(old_icon)
                except Exception:
                    pass
        except Exception:
            pass

        cursor.execute('DELETE FROM activities WHERE id = ?', (activity_row['id'],))
        conn.commit()
        conn.close()
        embed = discord.Embed(description=f'Activity **{name}** removed!', color=discord.Color.green())
        await interaction.response.send_message(embed=embed)
    except Exception as e:
        embed = discord.Embed(description=f'Error: {str(e)}', color=discord.Color.red())
        await interaction.response.send_message(embed=embed, ephemeral=True)

@activity.command(name='list', description='List all activities')
@app_commands.check(lambda i: check_channel(i))
async def activity_list(interaction: discord.Interaction):
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('SELECT id, name FROM activities ORDER BY name')
        activities = cursor.fetchall()
        conn.close()

        if not activities:
            await interaction.response.send_message('No activities found. Add one with `/activity add`')
            return

        embed = discord.Embed(title='Activities', color=discord.Color.blue())
        for activity_row in activities:
            embed.add_field(name=activity_row['name'], value='', inline=False)
        await interaction.response.send_message(embed=embed)
    except Exception as e:
        await interaction.response.send_message(f'Error: {str(e)}')

@activity.command(name='icon', description='Set an icon image for an activity')
@app_commands.describe(name='Name of the activity', image='Image to use as icon')
@app_commands.autocomplete(name=activity_autocomplete)
@app_commands.check(lambda i: check_channel(i))
async def activity_icon(interaction: discord.Interaction, name: str, image: discord.Attachment):
    if not is_admin_app(interaction):
        await interaction.response.send_message('You need to be an admin to use this command!', ephemeral=True)
        return

    if not image.content_type or not image.content_type.startswith('image/'):
        embed = discord.Embed(description='Please attach an image file!', color=discord.Color.red())
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    try:
        conn = get_db()
        cursor = conn.cursor()

        cursor.execute('SELECT id, icon_data FROM activities WHERE LOWER(name) = LOWER(?)', (name,))
        activity = cursor.fetchone()
        if not activity:
            embed = discord.Embed(description=f'Activity **{name}** not found!', color=discord.Color.red())
            await interaction.response.send_message(embed=embed, ephemeral=True)
            conn.close()
            return
        # delete existing local icon file if present (we'll replace it)
        try:
            old_icon = activity.get('icon_data') if 'icon_data' in activity.keys() else None
            if old_icon and not (old_icon.startswith('http://') or old_icon.startswith('https://')):
                try:
                    if os.path.exists(old_icon):
                        os.remove(old_icon)
                except Exception:
                    # ignore removal failures
                    pass
        except Exception:
            pass

        # persist the attachment to local storage to avoid ephemeral CDN URLs
        os.makedirs('activity_icons', exist_ok=True)
        try:
            data = await image.read()
            ext = os.path.splitext(image.filename)[1] or '.png'
            fname = f"{activity['id']}{ext}"
            path = os.path.join('activity_icons', fname)
            with open(path, 'wb') as f:
                f.write(data)
            cursor.execute('UPDATE activities SET icon_data = ? WHERE id = ?', (path, activity['id']))
        except Exception:
            # fallback to storing remote url if saving fails
            cursor.execute('UPDATE activities SET icon_data = ? WHERE id = ?', (image.url, activity['id']))

        conn.commit()
        # prepare embed and possible attachment (prefer the saved local path)
        file_obj, img_url = _prepare_icon_attachment(path)
        embed = discord.Embed(description=f'Icon set for **{name}**!', color=discord.Color.green())
        if img_url:
            embed.set_image(url=img_url)
        if file_obj:
            await interaction.response.send_message(embed=embed, file=file_obj)
        else:
            await interaction.response.send_message(embed=embed)

        # If the invoking user (not admin-target) hasn't confirmed a timezone, prompt them to select one
        try:
            if target_user.id == interaction.user.id:
                invoking_tz, tz_confirmed = get_user_timezone_and_confirmed(interaction.user.id)
                if not invoking_tz or not tz_confirmed:
                    class TimezoneSelectPrompt(discord.ui.View):
                        def __init__(self, *, timeout=120):
                            super().__init__(timeout=timeout)
                            options = [
                                discord.SelectOption(label='UTC', value='UTC'),
                                discord.SelectOption(label='America/New_York (US/Eastern)', value='America/New_York'),
                                discord.SelectOption(label='America/Chicago (US/Central)', value='America/Chicago'),
                                discord.SelectOption(label='America/Denver (US/Mountain)', value='America/Denver'),
                                discord.SelectOption(label='America/Los_Angeles (US/Pacific)', value='America/Los_Angeles'),
                                discord.SelectOption(label='America/Anchorage', value='America/Anchorage'),
                                discord.SelectOption(label='Pacific/Honolulu', value='Pacific/Honolulu'),
                                discord.SelectOption(label='Europe/London', value='Europe/London'),
                                discord.SelectOption(label='Europe/Paris', value='Europe/Paris'),
                                discord.SelectOption(label='Europe/Berlin', value='Europe/Berlin'),
                                discord.SelectOption(label='Europe/Moscow', value='Europe/Moscow'),
                                discord.SelectOption(label='Africa/Johannesburg', value='Africa/Johannesburg'),
                                discord.SelectOption(label='Asia/Dubai', value='Asia/Dubai'),
                                discord.SelectOption(label='Asia/Kolkata', value='Asia/Kolkata'),
                                discord.SelectOption(label='Asia/Bangkok', value='Asia/Bangkok'),
                                discord.SelectOption(label='Asia/Shanghai', value='Asia/Shanghai'),
                                discord.SelectOption(label='Asia/Tokyo', value='Asia/Tokyo'),
                                discord.SelectOption(label='Asia/Seoul', value='Asia/Seoul'),
                                discord.SelectOption(label='Australia/Sydney', value='Australia/Sydney'),
                                discord.SelectOption(label='America/Sao_Paulo', value='America/Sao_Paulo'),
                                discord.SelectOption(label='America/Mexico_City', value='America/Mexico_City'),
                                discord.SelectOption(label='America/Argentina/Buenos_Aires', value='America/Argentina/Buenos_Aires'),
                                discord.SelectOption(label='Other (type IANA)', value='other'),
                            ]

                            self.select = discord.ui.Select(placeholder='Select your timezone', min_values=1, max_values=1, options=options)
                            self.select.callback = self.on_select
                            self.add_item(self.select)

                        async def on_select(self, interaction: discord.Interaction):
                            val = self.select.values[0]
                            if val == 'other':
                                class TzModal(discord.ui.Modal, title='Enter IANA timezone'):
                                    tz_input = discord.ui.TextInput(label='Timezone (e.g. America/Los_Angeles)', style=discord.TextStyle.short)

                                    async def on_submit(modal_self, modal_interaction: discord.Interaction):
                                        tz_val = modal_self.tz_input.value.strip()
                                        try:
                                            ZoneInfo(tz_val)
                                        except Exception:
                                            await modal_interaction.response.send_message('Invalid timezone name.', ephemeral=True)
                                            return
                                        try:
                                            now_local = datetime.now()
                                            server_off = datetime.now().astimezone().utcoffset() or timedelta(0)
                                            actual_off = ZoneInfo(tz_val).utcoffset(now_local) or timedelta(0)
                                            delta = int((actual_off - server_off).total_seconds())
                                            if delta != 0:
                                                r = _shift_user_sessions_in_db(modal_interaction.user.id, delta)
                                            else:
                                                r = {'shifted': 0, 'errors': 0}
                                        except Exception:
                                            r = {'shifted': 0, 'errors': 1}
                                        set_user_timezone(modal_interaction.user.id, tz_val)
                                        await modal_interaction.response.send_message(f'Timezone set to **{tz_val}**. Migrated sessions: {r}.', ephemeral=True)

                                await interaction.response.send_modal(TzModal())
                                return

                            try:
                                ZoneInfo(val)
                                try:
                                    now_local = datetime.now()
                                    server_off = datetime.now().astimezone().utcoffset() or timedelta(0)
                                    actual_off = ZoneInfo(val).utcoffset(now_local) or timedelta(0)
                                    delta = int((actual_off - server_off).total_seconds())
                                    if delta != 0:
                                        r = _shift_user_sessions_in_db(interaction.user.id, delta)
                                    else:
                                        r = {'shifted': 0, 'errors': 0}
                                except Exception:
                                    r = {'shifted': 0, 'errors': 1}
                                set_user_timezone(interaction.user.id, val)
                                await interaction.response.send_message(f'Timezone set to **{val}**. Migrated sessions: {r}.', ephemeral=True)
                            except Exception:
                                await interaction.response.send_message('Failed to set timezone.', ephemeral=True)

                    view = TimezoneSelectPrompt()
                    invoking_tz, tz_confirmed = get_user_timezone_and_confirmed(interaction.user.id)
                    await interaction.followup.send('Please select your timezone (this will migrate your historical sessions).', ephemeral=True, view=view)
        except Exception:
            pass
    except Exception as e:
        embed = discord.Embed(description=f'Error: {str(e)}', color=discord.Color.red())
        await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name='clockin', description='Clock in to an activity')
@app_commands.describe(activity_name='Optional: name of the activity', user='Optional: mention a member to clock in (admin only)')
@app_commands.autocomplete(activity_name=activity_autocomplete)
@app_commands.check(lambda i: check_channel(i))
async def clockin(interaction: discord.Interaction, activity_name: str = None, user: discord.User = None):
    try:
        target_user = user if user else interaction.user

        if user and not is_admin_app(interaction):
            await interaction.response.send_message('Only admins can clock in other users!', ephemeral=True)
            return

        conn = get_db()
        cursor = conn.cursor()

        activity = None
        if activity_name:
            cursor.execute('SELECT id, name, icon_data FROM activities WHERE LOWER(name) = LOWER(?)', (activity_name,))
            activity = cursor.fetchone()
        else:
            # try guild default
            if interaction.guild:
                default_id = get_default_activity_for_guild(interaction.guild.id)
                if default_id:
                    cursor.execute('SELECT id, name, icon_data FROM activities WHERE id = ?', (default_id,))
                    activity = cursor.fetchone()
            # fallback to first activity
            if not activity:
                cursor.execute('SELECT id, name, icon_data FROM activities ORDER BY id LIMIT 1')
                activity = cursor.fetchone()

        if not activity:
            embed = discord.Embed(description=f'No activity specified and no default activities configured!', color=discord.Color.red())
            await interaction.response.send_message(embed=embed, ephemeral=True)
            conn.close()
            return

        cursor.execute('''
            SELECT id FROM sessions
            WHERE user_id = ?
              AND clock_out IS NULL
              AND (clock_in IS NOT NULL OR paused_at IS NOT NULL)
        ''', (target_user.id,))
        active = cursor.fetchone()

        if active:
            embed = discord.Embed(description=f'{target_user.mention} is already clocked in! Clock out first with `/clock out`', color=discord.Color.red())
            await interaction.response.send_message(embed=embed, ephemeral=True)
            conn.close()
            return

        now_iso = datetime.now().isoformat()
        cursor.execute('''
            INSERT INTO sessions (user_id, activity_id, date, duration_seconds, clock_in)
            VALUES (?, ?, ?, ?, ?)
        ''', (target_user.id, activity['id'], datetime.now().date().isoformat(), 0, now_iso))

        icon_url = activity['icon_data'] if 'icon_data' in activity.keys() else None
        conn.commit()
        conn.close()

        embed = discord.Embed(description=f'{target_user.mention} clocked in to **{activity["name"]}**', color=discord.Color.green())
        file_obj, img_url = _prepare_icon_attachment(icon_url)
        if img_url:
            embed.set_image(url=img_url)
        if file_obj:
            await interaction.response.send_message(embed=embed, file=file_obj)
        else:
            await interaction.response.send_message(embed=embed)
    except Exception as e:
        embed = discord.Embed(description=f'Error: {str(e)}', color=discord.Color.red())
        await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name='clockout', description='Clock out of current activity (optional signed duration adjustment)')
@app_commands.describe(time='Optional offset: minutes, M:S, or H:M:S (for example +5 or -0:02:00)', user='Optional: mention a member to clock out (admin only)')
@app_commands.check(lambda i: check_channel(i))
async def clockout(interaction: discord.Interaction, time: str = None, user: discord.User = None):
    try:
        target_user = user if user else interaction.user

        if user and not is_admin_app(interaction):
            await interaction.response.send_message('Only admins can clock out other users!', ephemeral=True)
            return

        conn = get_db()
        cursor = conn.cursor()

        cursor.execute('''
            SELECT sessions.id, activities.name, sessions.clock_in, sessions.duration_seconds
            FROM sessions
            JOIN activities ON sessions.activity_id = activities.id
            WHERE sessions.user_id = ? AND sessions.clock_out IS NULL AND sessions.clock_in IS NOT NULL
        ''', (target_user.id,))
        active = cursor.fetchone()

        delta_seconds = 0
        if time:
            try:
                delta_seconds = parse_hms_to_seconds(time)
            except ValueError:
                embed = discord.Embed(description='Invalid time. Use minutes, M:S, or H:M:S', color=discord.Color.red())
                await interaction.response.send_message(embed=embed, ephemeral=True)
                conn.close()
                return

        if active:
            # active session -> set clock_out to now +/- delta
            clock_out_time = datetime.now() + timedelta(seconds=delta_seconds)
            elapsed_seconds = int(round((clock_out_time - datetime.fromisoformat(active['clock_in'])).total_seconds()))
            seconds = active['duration_seconds'] + elapsed_seconds
            if seconds < 0:
                await interaction.response.send_message(
                    'The adjustment cannot make a session end before it started.', ephemeral=True
                )
                conn.close()
                return
            cursor.execute('''
                UPDATE sessions
                SET clock_out = ?, duration_seconds = ?
                WHERE id = ?
            ''', (clock_out_time.isoformat(), seconds, active['id']))
            conn.commit()
            time_str = format_time(seconds / 3600)

            cursor.execute(f'''
                SELECT SUM({session_duration_seconds_sql()}) AS total_seconds
                FROM sessions
                WHERE user_id = ? AND activity_id = (SELECT id FROM activities WHERE name = ?)
            ''', (clock_out_time.isoformat(), target_user.id, active['name']))
            total_row = cursor.fetchone()
            total_str = format_time((total_row['total_seconds'] or 0) / 3600)
            conn.close()

            embed = discord.Embed(description=f'{target_user.mention} clocked out of **{active["name"]}**', color=discord.Color.green())
            embed.add_field(name='Session', value=time_str, inline=True)
            embed.add_field(name='Total', value=total_str, inline=True)
            await interaction.response.send_message(embed=embed)
            return

        # no active session
        if time:
            # adjust most recent entry's clock_out by delta
            cursor.execute('''
                SELECT id, clock_out, clock_in FROM sessions
                WHERE user_id = ?
                ORDER BY clock_in DESC LIMIT 1
            ''', (target_user.id,))
            last = cursor.fetchone()
            if not last or not last['clock_out']:
                embed = discord.Embed(description=f'No completed session found to adjust for {target_user.mention}', color=discord.Color.red())
                await interaction.response.send_message(embed=embed, ephemeral=True)
                conn.close()
                return

            try:
                current_co = datetime.fromisoformat(last['clock_out'])
            except Exception:
                embed = discord.Embed(description='Failed to parse existing clock_out timestamp', color=discord.Color.red())
                await interaction.response.send_message(embed=embed, ephemeral=True)
                conn.close()
                return

            new_co = current_co + timedelta(seconds=delta_seconds)
            # update both clock_out and duration_seconds
            new_duration = int(round((new_co - datetime.fromisoformat(last['clock_in'])).total_seconds()))
            if new_duration < 0:
                await interaction.response.send_message(
                    'The adjustment cannot make a session end before it started.', ephemeral=True
                )
                conn.close()
                return
            cursor.execute('UPDATE sessions SET clock_out = ?, duration_seconds = ? WHERE id = ?', (new_co.isoformat(), new_duration, last['id']))
            conn.commit()
            conn.close()

            embed = discord.Embed(description=f'Adjusted last session for {target_user.mention} by {time}', color=discord.Color.green())
            await interaction.response.send_message(embed=embed)
            return

        embed = discord.Embed(description=f'{target_user.mention} is not clocked in!', color=discord.Color.red())
        await interaction.response.send_message(embed=embed)
        conn.close()
    except Exception as e:
        embed = discord.Embed(description=f'Error: {str(e)}', color=discord.Color.red())
        await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name='stats', description='View all your stats with heatmap')
@app_commands.check(lambda i: check_channel(i))
async def stats(interaction: discord.Interaction, user: discord.User = None):
    try:
        if user is None:
            user = interaction.user

        conn = get_db()
        cursor = conn.cursor()
        now = datetime.now()

        embeds = []

        periods = [
            ('today', now.date(), 'Today'),
            ('week', (now - timedelta(days=now.weekday())).date(), 'This Week'),
            ('all', None, 'All-Time'),
        ]

        # Use the same display style as leaderboard for clickable mentions
        display_name = await resolve_user_display(user.id, interaction.guild)
        embed_stats = discord.Embed(title='Time Tracking', color=discord.Color.blurple())
        embed_stats.add_field(name='User', value=display_name, inline=False)

        for _, start_date, label in periods:
            if start_date is None:
                cursor.execute(f'''
                    SELECT activities.name,
                           SUM({session_duration_seconds_sql()}) AS seconds
                    FROM sessions
                    JOIN activities ON sessions.activity_id = activities.id
                    WHERE sessions.user_id = ?
                    GROUP BY activities.name
                    ORDER BY seconds DESC
                ''', (now.isoformat(), user.id))
            else:
                start_iso = start_date.isoformat()
                cursor.execute(f'''
                    SELECT activities.name,
                           SUM({session_duration_seconds_sql()}) AS seconds
                    FROM sessions
                    JOIN activities ON sessions.activity_id = activities.id
                    WHERE sessions.user_id = ? AND sessions.date >= ?
                    GROUP BY activities.name
                    ORDER BY seconds DESC
                ''', (now.isoformat(), user.id, start_iso))

            stats_data = cursor.fetchall()

            if not stats_data:
                if label == 'Today':
                    continue
                embed_stats.add_field(name=label, value='No time tracked', inline=True)
            else:
                total_seconds = 0
                stats_text = ''
                single_activity = len(stats_data) == 1
                for row in stats_data:
                    seconds = row['seconds'] or 0
                    total_seconds += seconds
                    if single_activity:
                        stats_text += f"{format_time(seconds / 3600)}\n"
                    else:
                        stats_text += f"{row['name']}: {format_time(seconds / 3600)}\n"

                if len(stats_data) > 1:
                    stats_text += f"\n**Total: {format_time(total_seconds / 3600)}**"

                embed_stats.add_field(name=label, value=stats_text, inline=True)

        embeds.append(embed_stats)

        twelve_weeks_ago = (now - timedelta(weeks=12)).date()
        start_iso = twelve_weeks_ago.isoformat()
        cursor.execute(f'''
            SELECT sessions.date AS day,
                   SUM({session_duration_seconds_sql()}) AS seconds
            FROM sessions
            WHERE user_id = ? AND date >= ?
            GROUP BY sessions.date
            ORDER BY day
        ''', (now.isoformat(), user.id, start_iso))

        daily_stats = {row['day']: (row['seconds'] or 0) / 3600 for row in cursor.fetchall()}

        if daily_stats:
            heatmap = generate_heatmap(daily_stats, twelve_weeks_ago)
            embed_heatmap = discord.Embed(title='Activity Heatmap (Last 12 Weeks)', description=heatmap, color=discord.Color.green())
            embeds.append(embed_heatmap)

            cursor.execute(f'''
                SELECT activities.name,
                       SUM({session_duration_seconds_sql()}) AS seconds
                FROM sessions
                JOIN activities ON sessions.activity_id = activities.id
                WHERE sessions.user_id = ?
                GROUP BY activities.name
                ORDER BY seconds DESC, activities.name ASC
                LIMIT 1
            ''', (now.isoformat(), user.id))
            favorite_activity = cursor.fetchone()

            cursor.execute(f'''
                SELECT strftime('%w', date) AS day_of_week,
                       SUM({session_duration_seconds_sql()}) AS seconds
                FROM sessions
                WHERE user_id = ?
                GROUP BY day_of_week
                ORDER BY seconds DESC
            ''', (now.isoformat(), user.id))
            day_of_week_stats = cursor.fetchall()
            days = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday']
            most_active_day = days[int(day_of_week_stats[0]['day_of_week'])] if day_of_week_stats else 'N/A'
            most_active_seconds = day_of_week_stats[0]['seconds'] or 0 if day_of_week_stats else 0
            most_active_day += f' ({format_time(most_active_seconds / 3600)})'

            most_in_day = max(daily_stats.values())
            total_hours = sum(daily_stats.values())
            avg_per_day = total_hours / len(daily_stats) if daily_stats else 0
            # prefer timezone of the user prompting the command; if not set/confirmed, we will include a prompt view
            invoking_tz, tz_confirmed = get_user_timezone_and_confirmed(interaction.user.id)
            streak = calculate_streak(daily_stats, invoking_tz)

            embed_metrics = discord.Embed(title='Key Metrics', color=discord.Color.orange())
            embed_metrics.add_field(name='Streak', value=f'{streak} days', inline=True)
            embed_metrics.add_field(name='Max Day', value=format_time(most_in_day), inline=True)
            embed_metrics.add_field(name='Daily Avg', value=format_time(avg_per_day), inline=True)
            embed_metrics.add_field(name='Most Active', value=most_active_day, inline=True)
            if favorite_activity:
                embed_metrics.add_field(name='Favorite Activity', value=f"**{favorite_activity['name']}** ({format_time((favorite_activity['seconds'] or 0) / 3600)})", inline=True)
            embeds.append(embed_metrics)

        conn.close()
        # if user's timezone is missing or unconfirmed, attach a timezone-select view to the ephemeral response
        view = None
        try:
            invoking_tz, tz_confirmed = get_user_timezone_and_confirmed(interaction.user.id)
            if not invoking_tz or not tz_confirmed:
                # build timezone select view
                class TimezoneSelect(discord.ui.View):
                    def __init__(self, *, timeout=120):
                        super().__init__(timeout=timeout)
                        options = [
                            discord.SelectOption(label='UTC', value='UTC'),
                            discord.SelectOption(label='America/New_York (US/Eastern)', value='America/New_York'),
                            discord.SelectOption(label='America/Chicago (US/Central)', value='America/Chicago'),
                            discord.SelectOption(label='America/Denver (US/Mountain)', value='America/Denver'),
                            discord.SelectOption(label='America/Los_Angeles (US/Pacific)', value='America/Los_Angeles'),
                            discord.SelectOption(label='America/Anchorage', value='America/Anchorage'),
                            discord.SelectOption(label='Pacific/Honolulu', value='Pacific/Honolulu'),
                            discord.SelectOption(label='Europe/London', value='Europe/London'),
                            discord.SelectOption(label='Europe/Paris', value='Europe/Paris'),
                            discord.SelectOption(label='Europe/Berlin', value='Europe/Berlin'),
                            discord.SelectOption(label='Europe/Moscow', value='Europe/Moscow'),
                            discord.SelectOption(label='Africa/Johannesburg', value='Africa/Johannesburg'),
                            discord.SelectOption(label='Asia/Dubai', value='Asia/Dubai'),
                            discord.SelectOption(label='Asia/Kolkata', value='Asia/Kolkata'),
                            discord.SelectOption(label='Asia/Bangkok', value='Asia/Bangkok'),
                            discord.SelectOption(label='Asia/Shanghai', value='Asia/Shanghai'),
                            discord.SelectOption(label='Asia/Tokyo', value='Asia/Tokyo'),
                            discord.SelectOption(label='Asia/Seoul', value='Asia/Seoul'),
                            discord.SelectOption(label='Australia/Sydney', value='Australia/Sydney'),
                            discord.SelectOption(label='America/Sao_Paulo', value='America/Sao_Paulo'),
                            discord.SelectOption(label='America/Mexico_City', value='America/Mexico_City'),
                            discord.SelectOption(label='America/Argentina/Buenos_Aires', value='America/Argentina/Buenos_Aires'),
                            discord.SelectOption(label='Other (type IANA)', value='other'),
                        ]

                        self.select = discord.ui.Select(placeholder='Select your timezone', min_values=1, max_values=1, options=options)
                        self.select.callback = self.on_select
                        self.add_item(self.select)

                        async def on_select(self, interaction: discord.Interaction):
                            val = self.select.values[0]
                            if val == 'other':
                                # show modal to input IANA name
                                class TzModal(discord.ui.Modal, title='Enter IANA timezone'):
                                    tz_input = discord.ui.TextInput(label='Timezone (e.g. America/Los_Angeles)', style=discord.TextStyle.short)

                                    async def on_submit(modal_self, modal_interaction: discord.Interaction):
                                        tz_val = modal_self.tz_input.value.strip()
                                        try:
                                            ZoneInfo(tz_val)
                                        except Exception:
                                            await modal_interaction.response.send_message('Invalid timezone name.', ephemeral=True)
                                            return
                                        # compute shift from server-local time to selected timezone and migrate user's sessions
                                        try:
                                            now_local = datetime.now()
                                            server_off = datetime.now().astimezone().utcoffset() or timedelta(0)
                                            actual_off = ZoneInfo(tz_val).utcoffset(now_local) or timedelta(0)
                                            delta = int((actual_off - server_off).total_seconds())
                                            if delta != 0:
                                                r = _shift_user_sessions_in_db(modal_interaction.user.id, delta)
                                            else:
                                                r = {'shifted': 0, 'errors': 0}
                                        except Exception:
                                            r = {'shifted': 0, 'errors': 1}
                                        set_user_timezone(modal_interaction.user.id, tz_val)
                                        await modal_interaction.response.send_message(f'Timezone set to **{tz_val}**. Migrated sessions: {r}.', ephemeral=True)

                                await interaction.response.send_modal(TzModal())
                                return

                            # direct set
                            try:
                                ZoneInfo(val)
                                # compute shift and migrate
                                try:
                                    now_local = datetime.now()
                                    server_off = datetime.now().astimezone().utcoffset() or timedelta(0)
                                    actual_off = ZoneInfo(val).utcoffset(now_local) or timedelta(0)
                                    delta = int((actual_off - server_off).total_seconds())
                                    if delta != 0:
                                        r = _shift_user_sessions_in_db(interaction.user.id, delta)
                                    else:
                                        r = {'shifted': 0, 'errors': 0}
                                except Exception:
                                    r = {'shifted': 0, 'errors': 1}
                                set_user_timezone(interaction.user.id, val)
                                await interaction.response.send_message(f'Timezone set to **{val}**. Migrated sessions: {r}.', ephemeral=True)
                            except Exception:
                                await interaction.response.send_message('Failed to set timezone.', ephemeral=True)

                view = TimezoneSelect()
        except Exception:
            view = None

        # Send the main stats embed first
        await interaction.response.send_message(embeds=embeds, ephemeral=True,
                                                allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False))

        # If user's timezone is missing/unconfirmed, send a separate ephemeral follow-up with the timezone selector
        try:
            invoking_tz, tz_confirmed = get_user_timezone_and_confirmed(interaction.user.id)
            if not invoking_tz or not tz_confirmed:
                # view was constructed above as TimezoneSelect() if needed
                if view:
                    await interaction.followup.send('Please select your timezone (this will migrate your historical sessions).', ephemeral=True, view=view)
        except Exception:
            pass
    except Exception as e:
        await interaction.response.send_message(f'Error: {str(e)}', ephemeral=True)

@bot.tree.command(name='status', description='View your current status')
@app_commands.check(lambda i: check_channel(i))
async def status(interaction: discord.Interaction):
    try:
        conn = get_db()
        cursor = conn.cursor()

        cursor.execute('''
            SELECT activities.name, sessions.clock_in, sessions.duration_seconds, sessions.paused_at
            FROM sessions
            JOIN activities ON sessions.activity_id = activities.id
            WHERE sessions.user_id = ?
              AND sessions.clock_out IS NULL
              AND (sessions.clock_in IS NOT NULL OR sessions.paused_at IS NOT NULL)
        ''', (interaction.user.id,))

        active = cursor.fetchone()
        conn.close()

        if not active:
            await interaction.response.send_message(f'You are currently **not clocked in**', ephemeral=True)
            return

        seconds = active['duration_seconds']
        if active['clock_in']:
            seconds += int(round((datetime.now() - datetime.fromisoformat(active['clock_in'])).total_seconds()))

        embed = discord.Embed(title='Current Status', color=discord.Color.yellow())
        embed.add_field(name='Activity', value=active['name'], inline=False)
        embed.add_field(name='State', value='Paused' if active['paused_at'] else 'Clocked in', inline=False)
        embed.add_field(name='Duration', value=format_time(seconds / 3600), inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f'Error: {str(e)}')


async def session_pause(interaction: discord.Interaction):
    now = datetime.now()
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT sessions.id, sessions.clock_in, sessions.duration_seconds, sessions.paused_at, activities.name
            FROM sessions
            JOIN activities ON sessions.activity_id = activities.id
            WHERE sessions.user_id = ? AND sessions.clock_out IS NULL
              AND (sessions.clock_in IS NOT NULL OR sessions.paused_at IS NOT NULL)
        ''', (interaction.user.id,))
        active = cursor.fetchone()
        if not active:
            conn.close()
            await interaction.response.send_message('You are not clocked in.', ephemeral=True)
            return
        if active['paused_at']:
            conn.close()
            await interaction.response.send_message('Your clock is already paused.', ephemeral=True)
            return

        seconds = active['duration_seconds'] + int(round((now - datetime.fromisoformat(active['clock_in'])).total_seconds()))
        cursor.execute('''
            UPDATE sessions
            SET duration_seconds = ?, clock_in = NULL, paused_at = ?
            WHERE id = ?
        ''', (seconds, now.isoformat(), active['id']))
        conn.commit()
        conn.close()
        await interaction.response.send_message(
            f'Paused **{active["name"]}** at {format_time(seconds / 3600)}.', ephemeral=True
        )
    except Exception as e:
        await interaction.response.send_message(f'Error: {e}', ephemeral=True)


async def session_resume(interaction: discord.Interaction):
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT sessions.id, activities.name
            FROM sessions
            JOIN activities ON sessions.activity_id = activities.id
            WHERE sessions.user_id = ? AND sessions.clock_out IS NULL AND sessions.paused_at IS NOT NULL
        ''', (interaction.user.id,))
        paused = cursor.fetchone()
        if not paused:
            conn.close()
            await interaction.response.send_message('You do not have a paused clock.', ephemeral=True)
            return

        cursor.execute('UPDATE sessions SET clock_in = ?, paused_at = NULL WHERE id = ?',
                       (datetime.now().isoformat(), paused['id']))
        conn.commit()
        conn.close()
        await interaction.response.send_message(f'Resumed **{paused["name"]}**.', ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f'Error: {e}', ephemeral=True)

def generate_heatmap(daily_stats, start_date):
    now_date = datetime.now().date()
    weeks = []
    current_week = []
    week_start = start_date

    start_weekday = start_date.weekday()
    for _ in range(start_weekday):
        current_week.append('⬜')

    current_date = start_date
    while current_date <= now_date:
        day_str = current_date.strftime('%Y-%m-%d')
        hours = daily_stats.get(day_str, 0)

        if hours == 0:
            square = '⬜'
        elif hours < 2:
            square = '🟩'
        elif hours < 4:
            square = '🟨'
        elif hours < 6:
            square = '🟧'
        elif hours < 8:
            square = '🟥'
        else:
            square = '🟪'

        current_week.append(square)

        if len(current_week) == 7:
            weeks.append((week_start, ''.join(current_week)))
            week_start = current_date + timedelta(days=1)
            current_week = []

        current_date += timedelta(days=1)

    if current_week:
        weeks.append((week_start, ''.join(current_week)))

    def week_label(start_dt, end_dt):
        if start_dt.month == end_dt.month:
            return f"{start_dt.strftime('%b.')} {start_dt.day}-{end_dt.day}"
        return f"{start_dt.strftime('%b.')} {start_dt.day}-{end_dt.strftime('%b.')} {end_dt.day}"

    visible_rows = []
    for start_dt, cells in weeks:
        if all(cell == '⬜' for cell in cells):
            continue
        end_dt = start_dt + timedelta(days=len(cells) - 1)
        visible_rows.append((week_label(start_dt, end_dt), cells))

    if not visible_rows:
        return '```\nNo tracked time in this period.\n⬜ 0h  🟩 <2h  🟨 2-4h  🟧 4-6h  🟥 6h+\n```'

    max_label_width = max(len(label) for label, _ in visible_rows)
    padded_rows = [f"{label:<{max_label_width}} | {cells}" for label, cells in visible_rows]
    legend = '⬜ 0h  🟩 <2h  🟨 2-4h\n🟧 4-6h  🟥 6-8h  🟪 8h+'
    return f"```\n{'\n'.join(padded_rows)}\n{legend}\n```"

def calculate_streak(daily_stats: dict, tz: str | None = None) -> int:
    """Calculate consecutive-day streak ending today in the given timezone.

    Rules:
    - Streak only counts if the user has an entry for "today" in their local timezone.
      If they didn't track today (in their timezone), the streak is 0 (resets at midnight
      following the last tracked day).
    - Counts backwards day-by-day while dates exist in daily_stats.
    - `daily_stats` keys are 'YYYY-MM-DD' strings.
    """
    if not daily_stats:
        return 0

    # determine 'today' in user's timezone
    try:
        if tz:
            now_local = datetime.now(ZoneInfo(tz))
        else:
            now_local = datetime.now()
    except Exception:
        # fallback to naive server time
        now_local = datetime.now()

    today_str = now_local.date().isoformat()

    # If user hasn't tracked today in their local timezone, streak resets
    if today_str not in daily_stats:
        return 0

    # count consecutive days including today
    streak = 0
    current = now_local.date()
    while True:
        day_str = current.isoformat()
        if day_str in daily_stats:
            streak += 1
            current = current - timedelta(days=1)
        else:
            break

    return streak



@bot.tree.command(name='leaderboard', description='View leaderboard for an activity')
@app_commands.autocomplete(activity_name=activity_autocomplete)
@app_commands.check(lambda i: check_channel(i))
async def leaderboard(interaction: discord.Interaction, activity_name: str):
    try:
        conn = get_db()
        cursor = conn.cursor()

        cursor.execute('SELECT id, name FROM activities WHERE LOWER(name) = LOWER(?)', (activity_name,))
        activity = cursor.fetchone()
        if not activity:
            embed = discord.Embed(description=f'Activity **{activity_name}** not found!', color=discord.Color.red())
            await interaction.response.send_message(embed=embed, ephemeral=True)
            conn.close()
            return

        cursor.execute(f'''
            SELECT sessions.user_id,
                   SUM({session_duration_seconds_sql()}) AS seconds
            FROM sessions
            WHERE sessions.activity_id = ?
            GROUP BY sessions.user_id
            ORDER BY seconds DESC
        ''', (datetime.now().isoformat(), activity['id']))

        results = cursor.fetchall()
        conn.close()

        if not results:
            embed = discord.Embed(description=f'No one has tracked time for **{activity["name"]}** yet!', color=discord.Color.greyple())
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        embed = discord.Embed(title=f'{activity["name"]} - Leaderboard', color=discord.Color.gold())
        medals = ['1st', '2nd', '3rd']

        for idx, row in enumerate(results[:10]):
            user_id = row['user_id']
            hours = (row['seconds'] or 0) / 3600
            try:
                username = await resolve_user_display(user_id, interaction.guild)
            except:
                username = f'User {user_id}'

            medal = medals[idx] if idx < 3 else f'{idx + 1}.'
            time_str = format_time(hours)
            embed.add_field(name=f'{medal}', value=f'{username}\n{time_str}', inline=False)

        await interaction.response.send_message(embed=embed, ephemeral=True)
    except Exception as e:
        embed = discord.Embed(description=f'Error: {str(e)}', color=discord.Color.red())
        await interaction.response.send_message(embed=embed, ephemeral=True)

admin = app_commands.Group(name='admin', description='Manage admin roles')

tz_group = app_commands.Group(name='timezone', description='Manage your local timezone')


@tz_group.command(name='set', description='Set your timezone (IANA name, e.g. Europe/London)')
@app_commands.describe(tz='IANA timezone name (e.g. America/Los_Angeles)')
@app_commands.check(lambda i: check_channel(i))
async def timezone_set(interaction: discord.Interaction, tz: str):
    try:
        # validate
        try:
            ZoneInfo(tz)
        except ZoneInfoNotFoundError:
            await interaction.response.send_message('Invalid timezone name. Use an IANA timezone (e.g. America/Los_Angeles).', ephemeral=True)
            return

        set_user_timezone(interaction.user.id, tz)
        await interaction.response.send_message(f'Your timezone has been set to **{tz}**.', ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f'Error: {e}', ephemeral=True)


@tz_group.command(name='view', description='View your configured timezone')
@app_commands.check(lambda i: check_channel(i))
async def timezone_view(interaction: discord.Interaction):
    try:
        tz = get_user_timezone(interaction.user.id)
        if not tz:
            await interaction.response.send_message('No timezone configured. Set one with `/timezone set <tz>`', ephemeral=True)
            return
        await interaction.response.send_message(f'Your timezone is **{tz}**.', ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f'Error: {e}', ephemeral=True)

bot.tree.add_command(tz_group)

@admin.command(name='add', description='Add an admin role')
@app_commands.describe(role='Select a role to add as admin')
@app_commands.check(lambda i: check_channel(i))
async def admin_add(interaction: discord.Interaction, role: discord.Role):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message('Only server admins can manage admin roles!', ephemeral=True)
        return

    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('INSERT INTO admin_roles (guild_id, role_id) VALUES (?, ?)', (interaction.guild.id, role.id))
        conn.commit()
        conn.close()
        embed = discord.Embed(description=f'Added {role.mention} as an admin role!', color=discord.Color.green())
        await interaction.response.send_message(embed=embed)
    except sqlite3.IntegrityError:
        embed = discord.Embed(description=f'{role.mention} is already an admin role!', color=discord.Color.red())
        await interaction.response.send_message(embed=embed, ephemeral=True)
    except Exception as e:
        embed = discord.Embed(description=f'Error: {str(e)}', color=discord.Color.red())
        await interaction.response.send_message(embed=embed, ephemeral=True)

@admin.command(name='remove', description='Remove an admin role')
@app_commands.describe(role='Select a role to remove from admin')
@app_commands.check(lambda i: check_channel(i))
async def admin_remove(interaction: discord.Interaction, role: discord.Role):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message('Only server admins can manage admin roles!', ephemeral=True)
        return

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('DELETE FROM admin_roles WHERE guild_id = ? AND role_id = ?', (interaction.guild.id, role.id))
    conn.commit()
    deleted = cursor.rowcount
    conn.close()

    if deleted:
        embed = discord.Embed(description=f'Removed {role.mention} from admin roles!', color=discord.Color.green())
        await interaction.response.send_message(embed=embed)
    else:
        embed = discord.Embed(description=f'{role.mention} is not an admin role!', color=discord.Color.red())
        await interaction.response.send_message(embed=embed, ephemeral=True)


    def _parse_iso_ts(val: str) -> datetime | None:
        if not val:
            return None
        try:
            return datetime.fromisoformat(val)
        except Exception:
            try:
                return datetime.fromisoformat(val.replace('T', ' '))
            except Exception:
                try:
                    # fallback: treat as epoch seconds
                    if str(val).isdigit():
                        return datetime.fromtimestamp(int(val))
                except Exception:
                    return None
        return None


    def _shift_user_sessions_in_db(user_id: int, delta_seconds: int) -> dict:
        """Shift sessions for a user by delta_seconds. Returns summary dict."""
        if delta_seconds == 0:
            return {'shifted': 0, 'errors': 0}

        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('SELECT id, clock_in, clock_out FROM sessions WHERE user_id = ? AND (clock_in IS NOT NULL OR clock_out IS NOT NULL)', (user_id,))
        rows = cursor.fetchall()
        shifted = 0
        errors = 0
        for row in rows:
            sid = row['id']
            ci_raw = row['clock_in']
            co_raw = row['clock_out']
            try:
                ci = _parse_iso_ts(ci_raw) if ci_raw else None
                co = _parse_iso_ts(co_raw) if co_raw else None

                new_ci = (ci + timedelta(seconds=delta_seconds)) if ci else None
                new_co = (co + timedelta(seconds=delta_seconds)) if co else None

                if new_ci and new_co:
                    new_duration = int(round((new_co - new_ci).total_seconds()))
                    new_date = new_ci.date().isoformat()
                    cursor.execute('UPDATE sessions SET clock_in = ?, clock_out = ?, duration_seconds = ?, date = ? WHERE id = ?', (new_ci.isoformat(), new_co.isoformat(), new_duration, new_date, sid))
                elif new_ci and not new_co:
                    new_date = new_ci.date().isoformat()
                    cursor.execute('UPDATE sessions SET clock_in = ?, date = ? WHERE id = ?', (new_ci.isoformat(), new_date, sid))
                elif not new_ci and new_co:
                    # unlikely: only clock_out exists; shift clock_out and leave duration naive
                    cursor.execute('UPDATE sessions SET clock_out = ? WHERE id = ?', (new_co.isoformat(), sid))
                shifted += 1
            except Exception:
                errors += 1
                continue

        conn.commit()
        conn.close()
        return {'shifted': shifted, 'errors': errors}


    # @admin.command(name='import_timezones', description='Bulk import user timezones from a JSON file (admin only)')
    # @app_commands.describe(file='JSON file mapping user_id -> IANA timezone', assumed_tz='If provided, shift existing session timestamps from this assumed tz to the user tz')
    # @app_commands.check(lambda i: check_channel(i))
    async def _admin_import_timezones_removed(interaction: discord.Interaction, file: discord.Attachment, assumed_tz: str = None):
        if not is_admin_app(interaction):
            await interaction.response.send_message('Only admins can run this!', ephemeral=True)
            return

        try:
            raw = await file.read()
            import json
            mapping = json.loads(raw)
            if not isinstance(mapping, dict):
                await interaction.response.send_message('Invalid JSON format: expected object mapping user_id->timezone', ephemeral=True)
                return

            results = {'processed': 0, 'set': 0, 'shifted_total': 0, 'errors': 0}
            now = datetime.now()

            for uid_str, tz in mapping.items():
                # uid_str may be a numeric user ID or a username/display (name or name#discrim)
                uid = None
                try:
                    uid = int(uid_str)
                except Exception:
                    # attempt to resolve in the invoking guild's members
                    if interaction.guild:
                        member = None
                        for m in interaction.guild.members:
                            if m.name == uid_str or m.display_name == uid_str or f"{m.name}#{m.discriminator}" == uid_str:
                                member = m
                                break
                        if member:
                            uid = member.id
                    if uid is None:
                        results['errors'] += 1
                        continue

                # validate timezone
                try:
                    ZoneInfo(tz)
                except Exception:
                    results['errors'] += 1
                    continue

                # set timezone (confirmed)
                try:
                    set_user_timezone(uid, tz)
                    results['set'] += 1
                except Exception:
                    results['errors'] += 1
                    continue

                # if assumed_tz provided, compute delta from assumed->actual at current time and shift
                # (bulk import migration removed per request - migration will occur when the user confirms their timezone)

                results['processed'] += 1

            await interaction.response.send_message(f"Import complete: {results}", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f'Error importing: {e}', ephemeral=True)

@admin.command(name='list', description='List all admin roles')
@app_commands.check(lambda i: check_channel(i))
async def admin_list(interaction: discord.Interaction):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT role_id FROM admin_roles WHERE guild_id = ?', (interaction.guild.id,))
    role_ids = [row['role_id'] for row in cursor.fetchall()]
    conn.close()

    if not role_ids:
        embed = discord.Embed(description='No admin roles configured yet!', color=discord.Color.greyple())
        await interaction.response.send_message(embed=embed)
        return

    embed = discord.Embed(title='Admin Roles', color=discord.Color.blurple())
    for role_id in role_ids:
        role = interaction.guild.get_role(role_id)
        if role:
            embed.add_field(name=role.mention, value='', inline=False)
        else:
            embed.add_field(name=f'Unknown Role ({role_id})', value='*Role was deleted*', inline=False)

    await interaction.response.send_message(embed=embed)

@session.command(name='list', description='List recorded sessions for a user (optional, defaults to yourself)')
@app_commands.describe(user='Optional: mention a member to list sessions for')
@app_commands.check(lambda i: check_channel(i))
async def session_list(interaction: discord.Interaction, user: discord.User = None):
    try:
        target = user if user else interaction.user
        conn = get_db()
        cursor = conn.cursor()
        now = datetime.now().isoformat()
        cursor.execute(f'''
            SELECT sessions.id,
                   sessions.date,
                   activities.name AS activity_name,
                   {session_duration_seconds_sql()} AS duration_seconds,
                   sessions.note,
                   CASE
                       WHEN sessions.paused_at IS NOT NULL AND sessions.clock_out IS NULL THEN 'Paused'
                       WHEN sessions.clock_in IS NOT NULL AND sessions.clock_out IS NULL THEN 'In progress'
                       ELSE NULL
                   END AS state
            FROM sessions
            JOIN activities ON sessions.activity_id = activities.id
            WHERE sessions.user_id = ?
            ORDER BY sessions.date DESC, sessions.id DESC
        ''', (now, target.id))
        rows = cursor.fetchall()
        conn.close()

        if not rows:
            await interaction.response.send_message(f'No sessions found for {target.mention}', ephemeral=True)
            return

        unique_activities = {row['activity_name'] for row in rows}
        fields = build_session_list_fields(rows, hide_activity_name=len(unique_activities) == 1)

        embeds = []
        for start in range(0, len(fields), 25):
            page_fields = fields[start:start + 25]
            page_number = len(embeds) + 1
            embed = discord.Embed(
                title=f'Sessions for {target.mention}',
                description='Each entry shows **ID** · activity — duration.',
                color=discord.Color.blurple(),
            )
            if len(fields) > 25:
                embed.set_footer(text=f'Page {page_number} of {(len(fields) + 24) // 25}')
            for date, value in page_fields:
                embed.add_field(name=f'📅 {date}', value=value, inline=False)
            embeds.append(embed)

        await interaction.response.send_message(embeds=embeds, ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f'Error: {e}', ephemeral=True)


async def session_cancel(interaction: discord.Interaction, user: discord.User = None):
    try:
        target = user if user else interaction.user
        # if cancelling another user, require admin
        if user and not is_admin_app(interaction):
            await interaction.response.send_message('Only admins can cancel other users\' sessions.', ephemeral=True)
            return

        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT id, user_id, activity_id, clock_in, paused_at
            FROM sessions
            WHERE user_id = ? AND clock_out IS NULL
              AND (clock_in IS NOT NULL OR paused_at IS NOT NULL)
            ORDER BY id DESC
            LIMIT 1
        ''', (target.id,))
        row = cursor.fetchone()
        if not row:
            conn.close()
            if target == interaction.user:
                await interaction.response.send_message('You have no active session to cancel.', ephemeral=True)
            else:
                await interaction.response.send_message(f'{target.mention} has no active session to cancel.', ephemeral=True)
            return

        cursor.execute('DELETE FROM sessions WHERE id = ?', (row['id'],))
        conn.commit()
        conn.close()

        if target == interaction.user:
            await interaction.response.send_message('Your active session has been cancelled and removed.', ephemeral=True)
        else:
            await interaction.response.send_message(f'Cancelled active session for {target.mention}.', ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f'Error: {e}', ephemeral=True)


# Top-level aliases for session commands so users can use `/pause`, `/resume`, `/cancel`
@bot.tree.command(name='pause', description='Pause your current clock')
@app_commands.check(lambda i: check_channel(i))
async def pause(interaction: discord.Interaction):
    await session_pause(interaction)


@bot.tree.command(name='resume', description='Resume your paused clock')
@app_commands.check(lambda i: check_channel(i))
async def resume(interaction: discord.Interaction):
    await session_resume(interaction)


@bot.tree.command(name='cancel', description='Cancel and delete your active clock-in session (admin may specify a user)')
@app_commands.describe(user='Optional: mention a member to cancel their active session (admin only)')
@app_commands.check(lambda i: check_channel(i))
async def cancel(interaction: discord.Interaction, user: discord.User = None):
    await session_cancel(interaction, user)


@session.command(name='tag', description='Add a note to your active or selected session')
@app_commands.describe(note='Note to display with the session', id='Optional session ID; defaults to your active session')
@app_commands.check(lambda i: check_channel(i))
async def session_tag(interaction: discord.Interaction, note: str, id: int = None):
    note = note.strip()
    if not note:
        await interaction.response.send_message('A session note cannot be empty.', ephemeral=True)
        return
    if len(note) > 500:
        await interaction.response.send_message('Keep session notes to 500 characters or fewer.', ephemeral=True)
        return

    try:
        conn = get_db()
        cursor = conn.cursor()
        if id is None:
            cursor.execute('''
                SELECT id, user_id
                FROM sessions
                WHERE user_id = ? AND clock_out IS NULL
                  AND (clock_in IS NOT NULL OR paused_at IS NOT NULL)
            ''', (interaction.user.id,))
            session_row = cursor.fetchone()
            if not session_row:
                conn.close()
                await interaction.response.send_message(
                    'You have no active session. Provide a session ID to tag a recorded session.', ephemeral=True
                )
                return
        else:
            cursor.execute('SELECT id, user_id FROM sessions WHERE id = ?', (id,))
            session_row = cursor.fetchone()
            if not session_row:
                conn.close()
                await interaction.response.send_message('Session ID not found.', ephemeral=True)
                return
            if session_row['user_id'] != interaction.user.id and not is_admin_app(interaction):
                conn.close()
                await interaction.response.send_message(
                    'You can only tag your own sessions unless you are an admin.', ephemeral=True
                )
                return

        cursor.execute('UPDATE sessions SET note = ? WHERE id = ?', (note, session_row['id']))
        conn.commit()
        conn.close()
        await interaction.response.send_message(f'Added a note to session #{session_row["id"]}.', ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f'Error tagging session: {e}', ephemeral=True)


@session.command(name='combine', description='Combine sessions with matching activity and date')
@app_commands.describe(ids='Comma-separated session IDs, for example: 11, 12, 13')
@app_commands.check(lambda i: check_channel(i))
async def session_combine(interaction: discord.Interaction, ids: str):
    try:
        session_ids = parse_session_ids(ids)
    except ValueError as error:
        await interaction.response.send_message(str(error), ephemeral=True)
        return

    try:
        conn = get_db()
        cursor = conn.cursor()
        placeholders = ', '.join('?' for _ in session_ids)
        cursor.execute(f'''
            SELECT id, user_id, activity_id, date, duration_seconds, clock_in, clock_out, paused_at
            FROM sessions
            WHERE id IN ({placeholders})
        ''', session_ids)
        rows = cursor.fetchall()

        if len(rows) != len(session_ids):
            conn.close()
            await interaction.response.send_message('One or more session IDs were not found.', ephemeral=True)
            return
        if any(
            row['clock_out'] is None and (row['clock_in'] is not None or row['paused_at'] is not None)
            for row in rows
        ):
            conn.close()
            await interaction.response.send_message('Clock out active or paused sessions before combining them.', ephemeral=True)
            return

        owners = {row['user_id'] for row in rows}
        activities = {row['activity_id'] for row in rows}
        dates = {row['date'] for row in rows}
        if len(owners) != 1 or len(activities) != 1 or len(dates) != 1:
            conn.close()
            await interaction.response.send_message(
                'Sessions must belong to one user and have the same activity and date.', ephemeral=True
            )
            return
        if interaction.user.id not in owners and not is_admin_app(interaction):
            conn.close()
            await interaction.response.send_message('You can only combine your own sessions unless you are an admin.', ephemeral=True)
            return

        anchor_id = min(session_ids)
        total_seconds = sum(row['duration_seconds'] for row in rows)
        other_ids = [id_ for id_ in session_ids if id_ != anchor_id]
        cursor.execute('UPDATE sessions SET duration_seconds = ? WHERE id = ?', (total_seconds, anchor_id))
        other_placeholders = ', '.join('?' for _ in other_ids)
        cursor.execute(f'DELETE FROM sessions WHERE id IN ({other_placeholders})', other_ids)
        conn.commit()
        conn.close()
        await interaction.response.send_message(
            f'Combined {len(session_ids)} sessions into #{anchor_id} ({format_time(total_seconds / 3600)}).', ephemeral=True
        )
    except Exception as e:
        await interaction.response.send_message(f'Error combining sessions: {e}', ephemeral=True)


@session.command(name='add', description='Add a session for a date')
@app_commands.describe(duration='Duration: minutes, M:S, or H:M:S', date_str='Date: YYYY-MM-DD, MM-DD, or MM-DD-YYYY. Defaults to today', activity_name='Optional activity name', user='Optional: add for another user (admin only)')
@app_commands.autocomplete(activity_name=activity_autocomplete)
@app_commands.check(lambda i: check_channel(i))
async def session_add(interaction: discord.Interaction, duration: str, date_str: str = None, activity_name: str = None, user: discord.User = None):
    target_user = user if user else interaction.user
    if user and not is_admin_app(interaction):
        await interaction.response.send_message('Only admins can add sessions for other users!', ephemeral=True)
        return

    try:
        seconds = parse_hms_to_seconds(duration)
    except Exception:
        await interaction.response.send_message('Invalid duration. Use minutes, M:S, or H:M:S', ephemeral=True)
        return
    if seconds < 0:
        await interaction.response.send_message('Session duration cannot be negative.', ephemeral=True)
        return

    if date_str:
        try:
            date_obj = parse_date_input(date_str)
        except ValueError:
            await interaction.response.send_message('Invalid date. Use YYYY-MM-DD, MM-DD, or MM-DD-YYYY', ephemeral=True)
            return
    else:
        date_obj = datetime.now().date()

    conn = get_db()
    cursor = conn.cursor()

    # find activity
    activity = None
    if activity_name:
        cursor.execute('SELECT id FROM activities WHERE LOWER(name) = LOWER(?)', (activity_name,))
        activity = cursor.fetchone()
        if not activity:
            await interaction.response.send_message(f'Activity {activity_name} not found', ephemeral=True)
            conn.close()
            return
    else:
        if interaction.guild:
            default_id = get_default_activity_for_guild(interaction.guild.id)
            if default_id:
                cursor.execute('SELECT id FROM activities WHERE id = ?', (default_id,))
                activity = cursor.fetchone()
        if not activity:
            cursor.execute('SELECT id FROM activities ORDER BY id LIMIT 1')
            activity = cursor.fetchone()

    if not activity:
        await interaction.response.send_message('No activity configured to attach the session to', ephemeral=True)
        conn.close()
        return

    cursor.execute('INSERT INTO sessions (user_id, activity_id, date, duration_seconds) VALUES (?, ?, ?, ?)',
                   (target_user.id, activity['id'], date_obj.isoformat(), seconds))
    conn.commit()
    conn.close()

    await interaction.response.send_message(f'Session added for {target_user.mention} on {date_obj.isoformat()} ({duration})')


@session.command(name='remove', description='Remove a session by its numeric id')
@app_commands.describe(id='Session id to remove', user='Optional: target user (admin only)')
@app_commands.check(lambda i: check_channel(i))
async def session_remove(interaction: discord.Interaction, id: int, user: discord.User = None):
    target_user = user if user else interaction.user
    if user and not is_admin_app(interaction):
        await interaction.response.send_message('Only admins can remove sessions for other users!', ephemeral=True)
        return

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT id, user_id, date, duration_seconds FROM sessions WHERE id = ?', (id,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        await interaction.response.send_message('Session id not found', ephemeral=True)
        return

    if row['user_id'] != target_user.id and not is_admin_app(interaction):
        conn.close()
        await interaction.response.send_message('You can only remove your own sessions unless you are an admin.', ephemeral=True)
        return

    cursor.execute('DELETE FROM sessions WHERE id = ?', (id,))
    conn.commit()
    conn.close()

    await interaction.response.send_message(f'Removed session id {id} for {target_user.mention}', ephemeral=True)


@session.command(name='edit', description='Edit a session by id (change date and/or duration)')
@app_commands.describe(id='Session id to edit', date='Optional new date: YYYY-MM-DD, MM-DD, or MM-DD-YYYY', duration='Optional duration: minutes, M:S, or H:M:S')
@app_commands.check(lambda i: check_channel(i))
async def session_edit(interaction: discord.Interaction, id: int, date: str = None, duration: str = None):
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('SELECT id, user_id, date, duration_seconds FROM sessions WHERE id = ?', (id,))
        row = cursor.fetchone()
        if not row:
            conn.close()
            await interaction.response.send_message('Session id not found', ephemeral=True)
            return

        # permission: only owner or admin
        if row['user_id'] != interaction.user.id and not is_admin_app(interaction):
            conn.close()
            await interaction.response.send_message('You can only edit your own sessions unless you are an admin.', ephemeral=True)
            return

        updates = []
        params = []

        if date:
            try:
                date_obj = parse_date_input(date)
            except ValueError:
                conn.close()
                await interaction.response.send_message('Invalid date. Use YYYY-MM-DD, MM-DD, or MM-DD-YYYY', ephemeral=True)
                return
            updates.append('date = ?')
            params.append(date_obj.isoformat())

        if duration:
            try:
                seconds = parse_hms_to_seconds(duration)
            except Exception:
                conn.close()
                await interaction.response.send_message('Invalid duration. Use minutes, M:S, or H:M:S', ephemeral=True)
                return
            if seconds < 0:
                conn.close()
                await interaction.response.send_message('Session duration cannot be negative.', ephemeral=True)
                return
            updates.append('duration_seconds = ?')
            params.append(seconds)

        if not updates:
            conn.close()
            await interaction.response.send_message('Nothing to update. Provide `date` and/or `duration`.', ephemeral=True)
            return

        params.append(id)
        sql = f"UPDATE sessions SET {', '.join(updates)} WHERE id = ?"
        cursor.execute(sql, tuple(params))
        conn.commit()
        conn.close()

        await interaction.response.send_message(f'Session {id} updated.', ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f'Error editing session: {e}', ephemeral=True)


bot.tree.add_command(session)


quote = app_commands.Group(name='quote', description='Manage quotes')


@quote.command(name='add', description='Add a new quote')
@app_commands.describe(text='The quote text to add')
@app_commands.check(lambda i: check_channel(i))
async def quote_add(interaction: discord.Interaction, text: str):
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('INSERT INTO quotes (text) VALUES (?)', (text,))
        conn.commit()
        conn.close()
        await interaction.response.send_message('Quote added.', ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f'Error: {e}', ephemeral=True)


@quote.command(name='remove', description='Remove a quote by id')
@app_commands.describe(id='Quote id to remove')
@app_commands.check(lambda i: check_channel(i))
async def quote_remove(interaction: discord.Interaction, id: int):
    if not is_admin_app(interaction):
        await interaction.response.send_message('Only admins can remove quotes.', ephemeral=True)
        return
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('DELETE FROM quotes WHERE id = ?', (id,))
        deleted = cursor.rowcount
        conn.commit()
        conn.close()
        if deleted:
            await interaction.response.send_message('Quote removed.', ephemeral=True)
        else:
            await interaction.response.send_message('Quote not found.', ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f'Error: {e}', ephemeral=True)


@quote.command(name='list', description='List all quotes')
@app_commands.check(lambda i: check_channel(i))
async def quote_list(interaction: discord.Interaction):
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('SELECT id, text FROM quotes ORDER BY id ASC')
        rows = cursor.fetchall()
        conn.close()
        if not rows:
            await interaction.response.send_message('No quotes found.', ephemeral=True)
            return
        lines = [f"{r['id']}. {r['text']}" for r in rows]
        await interaction.response.send_message('\n'.join(lines[:200]), ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f'Error: {e}', ephemeral=True)


bot.tree.add_command(quote)


@bot.tree.command(name='gnaij', description='Say a random quote')
@app_commands.check(lambda i: check_channel(i))
async def gnaij(interaction: discord.Interaction):
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('SELECT id, text FROM quotes ORDER BY RANDOM() LIMIT 1')
        row = cursor.fetchone()
        conn.close()
        if not row:
            await interaction.response.send_message('No quotes available.', ephemeral=True)
            return
        # Public message
        await interaction.response.send_message(row['text'])
    except Exception as e:
        await interaction.response.send_message(f'Error: {e}', ephemeral=True)
@bot.tree.command(name='say', description='Make the bot say something')
@app_commands.check(lambda i: check_channel(i))
async def say(interaction: discord.Interaction, message: str):
    await interaction.response.send_message(message)


@activity.command(name='if', description='Estimate earnings for an activity at given hourly wage')
@app_commands.describe(activity_name='Activity name to evaluate', wage='Hourly wage (e.g. 10.5)')
@app_commands.autocomplete(activity_name=activity_autocomplete)
@app_commands.check(lambda i: check_channel(i))
async def activity_if(interaction: discord.Interaction, activity_name: str, wage: float):
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('SELECT id, name FROM activities WHERE LOWER(name) = LOWER(?)', (activity_name,))
        activity = cursor.fetchone()
        if not activity:
            conn.close()
            await interaction.response.send_message(f'Activity **{activity_name}** not found!', ephemeral=True)
            return

        # Sum duration per user in hours using sessions
        cursor.execute(f'''
            SELECT sessions.user_id, SUM({session_duration_seconds_sql()}) AS seconds
            FROM sessions
            WHERE sessions.activity_id = ?
            GROUP BY sessions.user_id
            ORDER BY seconds DESC
        ''', (datetime.now().isoformat(), activity['id']))

        rows = cursor.fetchall()
        conn.close()

        if not rows:
            await interaction.response.send_message(f'No recorded sessions for **{activity["name"]}**', ephemeral=True)
            return

        lines = [f'If for **{activity["name"]}** at ${wage:.2f}/hr:']
        for r in rows:
            uid = r['user_id']
            hours = (r['seconds'] or 0) / 3600
            earnings = hours * wage
            try:
                uname = await resolve_user_display(uid, interaction.guild)
            except:
                uname = f'User {uid}'
            lines.append(f'{uname}: {format_time(hours)} → ${earnings:,.2f}')

        # send ephemeral
        await interaction.response.send_message('\n'.join(lines[:200]), ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f'Error: {e}', ephemeral=True)


@activity.command(name='default', description='Set or view the guild default activity')
@app_commands.autocomplete(name=activity_autocomplete)
@app_commands.describe(name='Name of the activity to set as default (omit to view)')
@app_commands.check(lambda i: check_channel(i))
async def activity_default(interaction: discord.Interaction, name: str = None):
    if interaction.guild is None:
        await interaction.response.send_message('This command only works in a server (guild).', ephemeral=True)
        return

    try:
        conn = get_db()
        cursor = conn.cursor()
        if not name:
            default_id = get_default_activity_for_guild(interaction.guild.id)
            if not default_id:
                await interaction.response.send_message('No default activity set for this server.', ephemeral=True)
                conn.close()
                return
            cursor.execute('SELECT id, name, icon_data FROM activities WHERE id = ?', (default_id,))
            act = cursor.fetchone()
            conn.close()
            if not act:
                await interaction.response.send_message('Configured default activity could not be found.', ephemeral=True)
                return
            embed = discord.Embed(title='Default Activity', description=f'**{act["name"]}**', color=discord.Color.blurple())
            file_obj, img_url = _prepare_icon_attachment(act['icon_data'])
            if img_url:
                embed.set_image(url=img_url)
            if file_obj:
                await interaction.response.send_message(embed=embed, file=file_obj)
            else:
                await interaction.response.send_message(embed=embed)
            return

        # setting default requires admin
        if not is_admin_app(interaction):
            await interaction.response.send_message('You need to be an admin to set the default activity.', ephemeral=True)
            conn.close()
            return

        cursor.execute('SELECT id FROM activities WHERE LOWER(name) = LOWER(?)', (name,))
        row = cursor.fetchone()
        if not row:
            conn.close()
            await interaction.response.send_message(f'Activity **{name}** not found!', ephemeral=True)
            return

        set_default_activity_for_guild(interaction.guild.id, row['id'])
        await interaction.response.send_message(f'Default activity set to **{name}**', ephemeral=False)
    except Exception as e:
        await interaction.response.send_message(f'Error: {e}', ephemeral=True)


bot.tree.add_command(activity)

@bot.tree.command(name='commands', description='Show all available commands')
@app_commands.check(lambda i: check_channel(i))
async def commands_help(interaction: discord.Interaction):
    embed = discord.Embed(title='Time Tracking Bot Commands', color=discord.Color.blurple())

    sections = [
        ('Activity Management', [
            ('/activity add <name>', 'Add a new activity (admin only)'),
            ('/activity remove <name>', 'Remove an activity (admin only)'),
            ('/activity list', 'List all activities'),
            ('/activity icon <name> <image>', 'Set an icon for an activity (admin only)'),
            ('/activity if <name> <wage>', 'Estimate earnings for an activity'),
        ]),
        ('Time Tracking', [
                ('/clockin <activity> [user]', 'Clock in to an activity'),
                ('/clockout [user]', 'Clock out of current activity'),
                ('/session pause', 'Pause your current clock'),
                ('/session resume', 'Resume your paused clock'),
            ('/status', 'View your current status'),
        ]),
        ('Statistics', [
            ('/stats [user]', 'View stats with heatmap and metrics'),
            ('/leaderboard <activity>', 'View activity leaderboard'),
        ]),
        ('Admin Roles', [
            ('/admin add @role', 'Add an admin role'),
            ('/admin remove @role', 'Remove an admin role'),
            ('/admin list', 'List all admin roles'),
        ]),
        ('Bot Channels', [
            ('/channel add #channel', 'Allow bot in channel'),
            ('/channel remove #channel', 'Remove channel restriction'),
            ('/channel list', 'List allowed channels'),
        ]),
        ('Sessions', [
            ('/session add <duration> [date] [activity] [user]', 'Add a session using minutes, M:S, or H:M:S'),
            ('/session combine <ids>', 'Combine matching sessions, e.g. `11, 12, 13`'),
            ('/session remove <id> [user]', 'Remove a session by numeric id'),
            ('/session edit <id> [date] [duration]', 'Edit a session by id (change date and/or duration)'),
            ('/session list [user]', 'List sessions (defaults to yourself)'),
        ]),
    ]

    for section_title, commands_list in sections:
        section_text = '\n'.join([f'`{cmd}` — {desc}' for cmd, desc in commands_list])
        embed.add_field(name=section_title, value=section_text, inline=False)

    await interaction.response.send_message(embed=embed)

if __name__ == '__main__':
    init_db()
    sync_db_schema()
    bot.run(TOKEN)
