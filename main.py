import os
import sqlite3
from datetime import datetime, timedelta
from dotenv import load_dotenv
import discord
from discord.ext import commands
from discord import app_commands

load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix='.', intents=intents, help_command=None)

DB_PATH = 'time_tracker.db'

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

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS time_entries (
            id INTEGER PRIMARY KEY,
            user_id INTEGER NOT NULL,
            activity_id INTEGER NOT NULL,
            clock_in TIMESTAMP NOT NULL,
            clock_out TIMESTAMP,
            FOREIGN KEY (activity_id) REFERENCES activities (id)
        )
    ''')

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

    conn.commit()
    conn.close()

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

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
            print(f'✅ Synced {len(synced)} command(s) in {guild.name}')

        # Also sync globally for new servers
        synced = await bot.tree.sync()
        print(f'✅ Also synced globally ({len(synced)} commands)')
    except Exception as e:
        print(f'❌ Failed to sync commands: {type(e).__name__}: {e}')
        import traceback
        traceback.print_exc()
    print('Bot is ready.')

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

activity = app_commands.Group(name='activity', description='Manage activities')

@activity.command(name='create', description='Create a new activity/commitment to track')
@app_commands.check(lambda i: check_channel(i))
async def activity_create(interaction: discord.Interaction, name: str):
    if not is_admin_app(interaction):
        await interaction.response.send_message('❌ You need to be an admin to use this command!', ephemeral=True)
        return

    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('INSERT INTO activities (name) VALUES (?)', (name,))
        conn.commit()
        conn.close()
        embed = discord.Embed(description=f'✅ Activity **{name}** created!', color=discord.Color.green())
        await interaction.response.send_message(embed=embed)
    except sqlite3.IntegrityError:
        embed = discord.Embed(description=f'❌ Activity **{name}** already exists!', color=discord.Color.red())
        await interaction.response.send_message(embed=embed, ephemeral=True)
    except Exception as e:
        embed = discord.Embed(description=f'❌ Error: {str(e)}', color=discord.Color.red())
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
            await interaction.response.send_message('No activities found. Create one with `/activity create`')
            return

        embed = discord.Embed(title='Activities', color=discord.Color.blue())
        for activity_row in activities:
            embed.add_field(name=activity_row['name'], value='', inline=False)
        await interaction.response.send_message(embed=embed)
    except Exception as e:
        await interaction.response.send_message(f'❌ Error: {str(e)}')

@activity.command(name='delete', description='Delete an activity')
@app_commands.check(lambda i: check_channel(i))
async def activity_delete(interaction: discord.Interaction, name: str):
    if not is_admin_app(interaction):
        await interaction.response.send_message('❌ You need to be an admin to use this command!', ephemeral=True)
        return

    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('SELECT id FROM activities WHERE LOWER(name) = LOWER(?)', (name,))
        activity_row = cursor.fetchone()

        if not activity_row:
            embed = discord.Embed(description=f'❌ Activity **{name}** not found!', color=discord.Color.red())
            await interaction.response.send_message(embed=embed, ephemeral=True)
            conn.close()
            return

        cursor.execute('DELETE FROM activities WHERE id = ?', (activity_row['id'],))
        conn.commit()
        conn.close()
        embed = discord.Embed(description=f'✅ Activity **{name}** deleted!', color=discord.Color.green())
        await interaction.response.send_message(embed=embed)
    except Exception as e:
        embed = discord.Embed(description=f'❌ Error: {str(e)}', color=discord.Color.red())
        await interaction.response.send_message(embed=embed, ephemeral=True)

@activity.command(name='icon', description='Set an icon image for an activity')
@app_commands.describe(name='Name of the activity', image='Image to use as icon')
@app_commands.check(lambda i: check_channel(i))
async def activity_icon(interaction: discord.Interaction, name: str, image: discord.Attachment):
    if not is_admin_app(interaction):
        await interaction.response.send_message('❌ You need to be an admin to use this command!', ephemeral=True)
        return

    if not image.content_type or not image.content_type.startswith('image/'):
        embed = discord.Embed(description='❌ Please attach an image file!', color=discord.Color.red())
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    try:
        conn = get_db()
        cursor = conn.cursor()

        cursor.execute('SELECT id FROM activities WHERE LOWER(name) = LOWER(?)', (name,))
        activity = cursor.fetchone()
        if not activity:
            embed = discord.Embed(description=f'❌ Activity **{name}** not found!', color=discord.Color.red())
            await interaction.response.send_message(embed=embed, ephemeral=True)
            conn.close()
            return

        cursor.execute('UPDATE activities SET icon_data = ? WHERE id = ?', (image.url, activity['id']))
        conn.commit()
        conn.close()

        embed = discord.Embed(description=f'✅ Icon set for **{name}**!', color=discord.Color.green())
        embed.set_image(url=image.url)
        await interaction.response.send_message(embed=embed)
    except Exception as e:
        embed = discord.Embed(description=f'❌ Error: {str(e)}', color=discord.Color.red())
        await interaction.response.send_message(embed=embed, ephemeral=True)

bot.tree.add_command(activity)

@bot.tree.command(name='clockin', description='Clock in to an activity')
@app_commands.describe(activity_name='Name of the activity', user='Optional: mention a member to clock in (admin only)')
@app_commands.check(lambda i: check_channel(i))
async def clockin(interaction: discord.Interaction, activity_name: str, user: discord.User = None):
    try:
        target_user = user if user else interaction.user

        if user and not is_admin_app(interaction):
            await interaction.response.send_message('❌ Only admins can clock in other users!', ephemeral=True)
            return

        conn = get_db()
        cursor = conn.cursor()

        cursor.execute('SELECT id, name FROM activities WHERE LOWER(name) = LOWER(?)', (activity_name,))
        activity = cursor.fetchone()
        if not activity:
            embed = discord.Embed(description=f'❌ Activity **{activity_name}** not found!', color=discord.Color.red())
            await interaction.response.send_message(embed=embed, ephemeral=True)
            conn.close()
            return

        cursor.execute('''
            SELECT id FROM time_entries
            WHERE user_id = ? AND clock_out IS NULL
        ''', (target_user.id,))
        active = cursor.fetchone()

        if active:
            embed = discord.Embed(description=f'❌ {target_user.mention} is already clocked in! Clock out first with `/clockout`', color=discord.Color.red())
            await interaction.response.send_message(embed=embed, ephemeral=True)
            conn.close()
            return

        cursor.execute('''
            INSERT INTO time_entries (user_id, activity_id, clock_in)
            VALUES (?, ?, ?)
        ''', (target_user.id, activity['id'], datetime.now()))

        cursor.execute('SELECT icon_data FROM activities WHERE id = ?', (activity['id'],))
        icon_row = cursor.fetchone()
        conn.commit()
        conn.close()

        embed = discord.Embed(description=f'✅ {target_user.mention} clocked in to **{activity["name"]}**', color=discord.Color.green())
        if icon_row and icon_row['icon_data']:
            embed.set_image(url=icon_row['icon_data'])
        await interaction.response.send_message(embed=embed)
    except Exception as e:
        embed = discord.Embed(description=f'❌ Error: {str(e)}', color=discord.Color.red())
        await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name='clockout', description='Clock out of current activity')
@app_commands.describe(user='Optional: mention a member to clock out (admin only)')
@app_commands.check(lambda i: check_channel(i))
async def clockout(interaction: discord.Interaction, user: discord.User = None):
    try:
        target_user = user if user else interaction.user

        if user and not is_admin_app(interaction):
            await interaction.response.send_message('❌ Only admins can clock out other users!', ephemeral=True)
            return

        conn = get_db()
        cursor = conn.cursor()

        cursor.execute('''
            SELECT time_entries.id, activities.name, time_entries.clock_in
            FROM time_entries
            JOIN activities ON time_entries.activity_id = activities.id
            WHERE time_entries.user_id = ? AND time_entries.clock_out IS NULL
        ''', (target_user.id,))
        active = cursor.fetchone()

        if not active:
            embed = discord.Embed(description=f'❌ {target_user.mention} is not clocked in!', color=discord.Color.red())
            await interaction.response.send_message(embed=embed)
            conn.close()
            return

        cursor.execute('''
            UPDATE time_entries
            SET clock_out = ?
            WHERE id = ?
        ''', (datetime.now(), active['id']))
        conn.commit()

        duration = datetime.now() - datetime.fromisoformat(active['clock_in'])
        hours = duration.total_seconds() / 3600
        time_str = format_time(hours)

        cursor.execute('''
            SELECT SUM(CAST((JULIANDAY(COALESCE(clock_out, ?)) - JULIANDAY(clock_in)) * 24 AS REAL)) as total_hours
            FROM time_entries
            WHERE user_id = ? AND activity_id = (SELECT id FROM activities WHERE name = ?)
        ''', (datetime.now().isoformat(), target_user.id, active['name']))
        total_row = cursor.fetchone()
        total_hours = total_row['total_hours'] or 0
        total_str = format_time(total_hours)
        conn.close()

        embed = discord.Embed(description=f'✅ {target_user.mention} clocked out of **{active["name"]}**', color=discord.Color.green())
        embed.add_field(name='Session', value=time_str, inline=True)
        embed.add_field(name='Total', value=total_str, inline=True)
        await interaction.response.send_message(embed=embed)
    except Exception as e:
        embed = discord.Embed(description=f'❌ Error: {str(e)}', color=discord.Color.red())
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
            ('today', now.replace(hour=0, minute=0, second=0, microsecond=0), 'Today'),
            ('week', now - timedelta(days=now.weekday()), 'This Week'),
            ('all', datetime.min, 'All-Time'),
        ]

        embed_stats = discord.Embed(title=f"{user.name}'s Time Tracking", color=discord.Color.blurple())

        for period, start_time, label in periods:
            if period != 'all':
                start_time = start_time.replace(hour=0, minute=0, second=0, microsecond=0)

            cursor.execute('''
                SELECT activities.name,
                       SUM(CAST((JULIANDAY(COALESCE(time_entries.clock_out, ?)) - JULIANDAY(time_entries.clock_in)) * 24 AS REAL)) as hours
                FROM time_entries
                JOIN activities ON time_entries.activity_id = activities.id
                WHERE time_entries.user_id = ? AND time_entries.clock_in >= ?
                GROUP BY activities.name
                ORDER BY hours DESC
            ''', (now.isoformat(), user.id, start_time.isoformat()))

            stats_data = cursor.fetchall()

            if not stats_data:
                embed_stats.add_field(name=label, value='No time tracked', inline=True)
            else:
                total_hours = 0
                stats_text = ''
                for row in stats_data:
                    hours = row['hours'] or 0
                    total_hours += hours
                    stats_text += f"{row['name']}: {format_time(hours)}\n"
                stats_text += f"\n**Total: {format_time(total_hours)}**"
                embed_stats.add_field(name=label, value=stats_text, inline=True)

        embeds.append(embed_stats)

        twelve_weeks_ago = now - timedelta(weeks=12)
        cursor.execute('''
            SELECT DATE(clock_in) as day,
                   SUM(CAST((JULIANDAY(COALESCE(clock_out, ?)) - JULIANDAY(clock_in)) * 24 AS REAL)) as hours
            FROM time_entries
            WHERE user_id = ? AND clock_in >= ?
            GROUP BY DATE(clock_in)
            ORDER BY day
        ''', (now.isoformat(), user.id, twelve_weeks_ago.isoformat()))

        daily_stats = {row['day']: row['hours'] or 0 for row in cursor.fetchall()}

        if daily_stats:
            heatmap = generate_heatmap(daily_stats, twelve_weeks_ago)
            embed_heatmap = discord.Embed(title='Activity Heatmap (Last 12 Weeks)', description=heatmap, color=discord.Color.green())
            embeds.append(embed_heatmap)

            cursor.execute('''
                SELECT strftime('%w', clock_in) as day_of_week,
                       SUM(CAST((JULIANDAY(COALESCE(clock_out, ?)) - JULIANDAY(clock_in)) * 24 AS REAL)) as hours
                FROM time_entries
                WHERE user_id = ?
                GROUP BY day_of_week
                ORDER BY hours DESC
            ''', (now.isoformat(), user.id))
            day_of_week_stats = cursor.fetchall()
            days = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday']
            most_active_day = days[int(day_of_week_stats[0]['day_of_week'])] if day_of_week_stats else 'N/A'
            most_active_hours = day_of_week_stats[0]['hours'] or 0 if day_of_week_stats else 0
            most_active_day += f' ({format_time(most_active_hours)})'

            most_in_day = max(daily_stats.values())
            total_hours = sum(daily_stats.values())
            avg_per_day = total_hours / len(daily_stats) if daily_stats else 0
            streak = calculate_streak(daily_stats)

            embed_metrics = discord.Embed(title='Key Metrics', color=discord.Color.orange())
            embed_metrics.add_field(name='Streak', value=f'{streak} days', inline=True)
            embed_metrics.add_field(name='Max Day', value=format_time(most_in_day), inline=True)
            embed_metrics.add_field(name='Daily Avg', value=format_time(avg_per_day), inline=True)
            embed_metrics.add_field(name='Most Active', value=most_active_day, inline=True)
            embeds.append(embed_metrics)

        conn.close()
        for embed in embeds:
            await interaction.followup.send(embed=embed) if interaction.response.is_done() else await interaction.response.send_message(embed=embed)
    except Exception as e:
        await interaction.response.send_message(f'❌ Error: {str(e)}', ephemeral=True)

@bot.tree.command(name='status', description='View your current status')
@app_commands.check(lambda i: check_channel(i))
async def status(interaction: discord.Interaction):
    try:
        conn = get_db()
        cursor = conn.cursor()

        cursor.execute('''
            SELECT activities.name, time_entries.clock_in
            FROM time_entries
            JOIN activities ON time_entries.activity_id = activities.id
            WHERE time_entries.user_id = ? AND time_entries.clock_out IS NULL
        ''', (interaction.user.id,))

        active = cursor.fetchone()
        conn.close()

        if not active:
            await interaction.response.send_message(f'You are currently **not clocked in**')
            return

        clock_in = datetime.fromisoformat(active['clock_in'])
        duration = datetime.now() - clock_in
        hours = duration.total_seconds() / 3600

        embed = discord.Embed(title='Current Status', color=discord.Color.yellow())
        embed.add_field(name='Activity', value=active['name'], inline=False)
        embed.add_field(name='Duration', value=format_time(hours), inline=False)
        await interaction.response.send_message(embed=embed)
    except Exception as e:
        await interaction.response.send_message(f'❌ Error: {str(e)}')

def generate_heatmap(daily_stats, start_date):
    now = datetime.now()
    weeks = []
    current_week = []

    start_weekday = start_date.weekday()
    for _ in range(start_weekday):
        current_week.append('⬜')

    current_date = start_date
    while current_date <= now:
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
        else:
            square = '🟥'

        current_week.append(square)

        if len(current_week) == 7:
            weeks.append(''.join(current_week))
            current_week = []

        current_date += timedelta(days=1)

    if current_week:
        weeks.append(''.join(current_week))

    heatmap_str = '\n'.join(weeks[-12:])
    legend = '⬜ 0h  🟩 <2h  🟨 2-4h  🟧 4-6h  🟥 6h+'
    return f'{heatmap_str}\n{legend}'

def calculate_streak(daily_stats):
    if not daily_stats:
        return 0

    sorted_days = sorted(daily_stats.keys(), reverse=True)
    today = datetime.now().strftime('%Y-%m-%d')

    streak = 0
    current_date = datetime.fromisoformat(today)

    while True:
        day_str = current_date.strftime('%Y-%m-%d')
        if day_str in daily_stats:
            streak += 1
            current_date -= timedelta(days=1)
        else:
            break

    return streak

def format_time(hours):
    total_seconds = int(hours * 3600)
    hours_part = total_seconds // 3600
    minutes_part = (total_seconds % 3600) // 60
    seconds_part = total_seconds % 60

    if seconds_part == 0:
        return f'{hours_part}h:{minutes_part:02d}m'
    else:
        return f'{hours_part}h:{minutes_part:02d}m:{seconds_part:02d}s'

@bot.tree.command(name='leaderboard', description='View leaderboard for an activity')
@app_commands.check(lambda i: check_channel(i))
async def leaderboard(interaction: discord.Interaction, activity_name: str):
    try:
        conn = get_db()
        cursor = conn.cursor()

        cursor.execute('SELECT id, name FROM activities WHERE LOWER(name) = LOWER(?)', (activity_name,))
        activity = cursor.fetchone()
        if not activity:
            embed = discord.Embed(description=f'❌ Activity **{activity_name}** not found!', color=discord.Color.red())
            await interaction.response.send_message(embed=embed)
            conn.close()
            return

        cursor.execute('''
            SELECT time_entries.user_id,
                   SUM(CAST((JULIANDAY(COALESCE(time_entries.clock_out, ?)) - JULIANDAY(time_entries.clock_in)) * 24 AS REAL)) as hours
            FROM time_entries
            WHERE time_entries.activity_id = ?
            GROUP BY time_entries.user_id
            ORDER BY hours DESC
        ''', (datetime.now().isoformat(), activity['id']))

        results = cursor.fetchall()
        conn.close()

        if not results:
            embed = discord.Embed(description=f'No one has tracked time for **{activity["name"]}** yet!', color=discord.Color.greyple())
            await interaction.response.send_message(embed=embed)
            return

        embed = discord.Embed(title=f'{activity["name"]} - Leaderboard', color=discord.Color.gold())
        medals = ['🥇', '🥈', '🥉']

        for idx, row in enumerate(results[:10]):
            user_id = row['user_id']
            hours = row['hours'] or 0
            try:
                user = await bot.fetch_user(user_id)
                username = user.name
            except:
                username = f'User {user_id}'

            medal = medals[idx] if idx < 3 else f'{idx + 1}.'
            time_str = format_time(hours)
            embed.add_field(name=f'{medal} {username}', value=time_str, inline=False)

        await interaction.response.send_message(embed=embed)
    except Exception as e:
        embed = discord.Embed(description=f'❌ Error: {str(e)}', color=discord.Color.red())
        await interaction.response.send_message(embed=embed, ephemeral=True)

admin = app_commands.Group(name='admin', description='Manage admin roles')

@admin.command(name='add', description='Add an admin role')
@app_commands.describe(role='Select a role to add as admin')
@app_commands.check(lambda i: check_channel(i))
async def admin_add(interaction: discord.Interaction, role: discord.Role):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message('❌ Only server admins can manage admin roles!', ephemeral=True)
        return

    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('INSERT INTO admin_roles (guild_id, role_id) VALUES (?, ?)', (interaction.guild.id, role.id))
        conn.commit()
        conn.close()
        embed = discord.Embed(description=f'✅ Added {role.mention} as an admin role!', color=discord.Color.green())
        await interaction.response.send_message(embed=embed)
    except sqlite3.IntegrityError:
        embed = discord.Embed(description=f'❌ {role.mention} is already an admin role!', color=discord.Color.red())
        await interaction.response.send_message(embed=embed, ephemeral=True)
    except Exception as e:
        embed = discord.Embed(description=f'❌ Error: {str(e)}', color=discord.Color.red())
        await interaction.response.send_message(embed=embed, ephemeral=True)

@admin.command(name='remove', description='Remove an admin role')
@app_commands.describe(role='Select a role to remove from admin')
@app_commands.check(lambda i: check_channel(i))
async def admin_remove(interaction: discord.Interaction, role: discord.Role):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message('❌ Only server admins can manage admin roles!', ephemeral=True)
        return

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('DELETE FROM admin_roles WHERE guild_id = ? AND role_id = ?', (interaction.guild.id, role.id))
    conn.commit()
    deleted = cursor.rowcount
    conn.close()

    if deleted:
        embed = discord.Embed(description=f'✅ Removed {role.mention} from admin roles!', color=discord.Color.green())
        await interaction.response.send_message(embed=embed)
    else:
        embed = discord.Embed(description=f'❌ {role.mention} is not an admin role!', color=discord.Color.red())
        await interaction.response.send_message(embed=embed, ephemeral=True)

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

bot.tree.add_command(admin)

channel = app_commands.Group(name='channel', description='Manage bot channels')

@channel.command(name='add', description='Add a channel where the bot can work')
@app_commands.describe(channel='Select a channel to allow bot access')
@app_commands.check(lambda i: check_channel(i))
async def channel_add(interaction: discord.Interaction, channel: discord.TextChannel):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message('❌ Only server admins can manage bot channels!', ephemeral=True)
        return

    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('INSERT INTO allowed_channels (guild_id, channel_id) VALUES (?, ?)', (interaction.guild.id, channel.id))
        conn.commit()
        conn.close()
        embed = discord.Embed(description=f'✅ Bot is now allowed in {channel.mention}', color=discord.Color.green())
        await interaction.response.send_message(embed=embed)
    except sqlite3.IntegrityError:
        embed = discord.Embed(description=f'❌ Bot is already allowed in {channel.mention}!', color=discord.Color.red())
        await interaction.response.send_message(embed=embed, ephemeral=True)
    except Exception as e:
        embed = discord.Embed(description=f'❌ Error: {str(e)}', color=discord.Color.red())
        await interaction.response.send_message(embed=embed, ephemeral=True)

@channel.command(name='remove', description='Remove a channel where the bot can work')
@app_commands.describe(channel='Select a channel to remove from bot access')
@app_commands.check(lambda i: check_channel(i))
async def channel_remove(interaction: discord.Interaction, channel: discord.TextChannel):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message('❌ Only server admins can manage bot channels!', ephemeral=True)
        return

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('DELETE FROM allowed_channels WHERE guild_id = ? AND channel_id = ?', (interaction.guild.id, channel.id))
    conn.commit()
    deleted = cursor.rowcount
    conn.close()

    if deleted:
        embed = discord.Embed(description=f'✅ Bot is no longer allowed in {channel.mention}', color=discord.Color.green())
        await interaction.response.send_message(embed=embed)
    else:
        embed = discord.Embed(description=f'❌ Bot is not restricted to {channel.mention}', color=discord.Color.red())
        await interaction.response.send_message(embed=embed, ephemeral=True)

@channel.command(name='list', description='List all allowed channels')
@app_commands.check(lambda i: check_channel(i))
async def channel_list(interaction: discord.Interaction):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT channel_id FROM allowed_channels WHERE guild_id = ?', (interaction.guild.id,))
    channel_ids = [row['channel_id'] for row in cursor.fetchall()]
    conn.close()

    if not channel_ids:
        embed = discord.Embed(description='No channel restrictions set! Bot works in all channels.', color=discord.Color.greyple())
        await interaction.response.send_message(embed=embed)
        return

    embed = discord.Embed(title='Allowed Bot Channels', color=discord.Color.blurple())
    for channel_id in channel_ids:
        ch = interaction.guild.get_channel(channel_id)
        if ch:
            embed.add_field(name=ch.mention, value='', inline=False)
        else:
            embed.add_field(name=f'Unknown Channel ({channel_id})', value='*Channel was deleted*', inline=False)

    await interaction.response.send_message(embed=embed)

bot.tree.add_command(channel)

time = app_commands.Group(name='time', description='Manage time entries (admin only)')

@time.command(name='add', description='Add time to a user for an activity')
@app_commands.describe(user='Mention the member', activity_name='Name of the activity', minutes='Minutes to add', date_str='Optional date (YYYY-MM-DD, today, yesterday)')
@app_commands.check(lambda i: check_channel(i))
async def time_add(interaction: discord.Interaction, user: discord.User, activity_name: str, minutes: float, date_str: str = None):
    if not is_admin_app(interaction):
        await interaction.response.send_message('❌ Only admins can use this command!', ephemeral=True)
        return

    try:
        conn = get_db()
        cursor = conn.cursor()

        cursor.execute('SELECT id FROM activities WHERE LOWER(name) = LOWER(?)', (activity_name,))
        activity = cursor.fetchone()
        if not activity:
            embed = discord.Embed(description=f'❌ Activity **{activity_name}** not found!', color=discord.Color.red())
            await interaction.response.send_message(embed=embed)
            conn.close()
            return

        if date_str:
            try:
                clock_in = datetime.fromisoformat(date_str)
            except:
                try:
                    clock_in = datetime.strptime(date_str, '%Y-%m-%d %H:%M')
                except:
                    try:
                        if date_str.lower() == 'today':
                            clock_in = datetime.now()
                        elif date_str.lower() == 'yesterday':
                            clock_in = datetime.now() - timedelta(days=1)
                        else:
                            clock_in = datetime.strptime(date_str, '%Y-%m-%d')
                    except:
                        embed = discord.Embed(description='❌ Invalid date format! Use: YYYY-MM-DD HH:MM or "today" or "yesterday"', color=discord.Color.red())
                        await interaction.response.send_message(embed=embed)
                        conn.close()
                        return
        else:
            clock_in = datetime.now()

        clock_out = clock_in + timedelta(minutes=minutes)

        cursor.execute('''
            INSERT INTO time_entries (user_id, activity_id, clock_in, clock_out)
            VALUES (?, ?, ?, ?)
        ''', (user.id, activity['id'], clock_in.isoformat(), clock_out.isoformat()))
        conn.commit()
        conn.close()

        embed = discord.Embed(description=f'✅ Added {minutes}m to {user.mention} for **{activity_name}** (recorded at {clock_in.strftime("%Y-%m-%d %H:%M")})', color=discord.Color.green())
        await interaction.response.send_message(embed=embed)
    except Exception as e:
        embed = discord.Embed(description=f'❌ Error: {str(e)}', color=discord.Color.red())
        await interaction.response.send_message(embed=embed, ephemeral=True)

@time.command(name='remove', description='Remove time from a user for an activity')
@app_commands.describe(user='Mention the member', activity_name='Name of the activity', minutes='Minutes to remove', date_str='Optional date (YYYY-MM-DD, today, yesterday)')
@app_commands.check(lambda i: check_channel(i))
async def time_remove(interaction: discord.Interaction, user: discord.User, activity_name: str, minutes: float, date_str: str = None):
    if not is_admin_app(interaction):
        await interaction.response.send_message('❌ Only admins can use this command!', ephemeral=True)
        return

    try:
        conn = get_db()
        cursor = conn.cursor()

        cursor.execute('SELECT id FROM activities WHERE LOWER(name) = LOWER(?)', (activity_name,))
        activity = cursor.fetchone()
        if not activity:
            embed = discord.Embed(description=f'❌ Activity **{activity_name}** not found!', color=discord.Color.red())
            await interaction.response.send_message(embed=embed)
            conn.close()
            return

        if date_str:
            try:
                target_date = datetime.fromisoformat(date_str)
            except:
                try:
                    target_date = datetime.strptime(date_str, '%Y-%m-%d %H:%M')
                except:
                    try:
                        if date_str.lower() == 'today':
                            target_date = datetime.now()
                        elif date_str.lower() == 'yesterday':
                            target_date = datetime.now() - timedelta(days=1)
                        else:
                            target_date = datetime.strptime(date_str, '%Y-%m-%d')
                    except:
                        embed = discord.Embed(description='❌ Invalid date format! Use: YYYY-MM-DD HH:MM or "today" or "yesterday"', color=discord.Color.red())
                        await interaction.response.send_message(embed=embed)
                        conn.close()
                        return

            cursor.execute('''
                SELECT id FROM time_entries
                WHERE user_id = ? AND activity_id = ? AND DATE(clock_in) = DATE(?)
                ORDER BY clock_in DESC LIMIT 1
            ''', (user.id, activity['id'], target_date.isoformat()))
        else:
            cursor.execute('''
                SELECT id FROM time_entries
                WHERE user_id = ? AND activity_id = ?
                ORDER BY clock_in DESC LIMIT 1
            ''', (user.id, activity['id']))

        entry = cursor.fetchone()
        if not entry:
            embed = discord.Embed(description=f'❌ No time entry found for {user.mention} on **{activity_name}**', color=discord.Color.red())
            await interaction.response.send_message(embed=embed)
            conn.close()
            return

        cursor.execute('''
            UPDATE time_entries
            SET clock_out = DATETIME(clock_out, '-' || ? || ' minutes')
            WHERE id = ?
        ''', (minutes, entry['id']))
        conn.commit()
        conn.close()

        embed = discord.Embed(description=f'✅ Removed {minutes}m from {user.mention} for **{activity_name}**', color=discord.Color.green())
        await interaction.response.send_message(embed=embed)
    except Exception as e:
        embed = discord.Embed(description=f'❌ Error: {str(e)}', color=discord.Color.red())
        await interaction.response.send_message(embed=embed, ephemeral=True)

bot.tree.add_command(time)

@bot.tree.command(name='say', description='Make the bot say something')
@app_commands.check(lambda i: check_channel(i))
async def say(interaction: discord.Interaction, message: str):
    await interaction.response.send_message(message)

@bot.tree.command(name='commands', description='Show all available commands')
@app_commands.check(lambda i: check_channel(i))
async def commands_help(interaction: discord.Interaction):
    embed = discord.Embed(title='Time Tracking Bot Commands', color=discord.Color.blurple())

    sections = [
        ('Activity Management', [
            ('/activity create <name>', 'Create a new activity (admin only)'),
            ('/activity list', 'List all activities'),
            ('/activity delete <name>', 'Delete an activity (admin only)'),
            ('/activity icon <name> <image>', 'Set an icon for an activity (admin only)'),
        ]),
        ('Time Tracking', [
            ('/clockin <activity> [user]', 'Clock in to an activity'),
            ('/clockout [user]', 'Clock out of current activity'),
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
        ('Time Management', [
            ('/time add @user <activity> <minutes> [date]', 'Add time to a user (admin only)'),
            ('/time remove @user <activity> <minutes> [date]', 'Remove time from a user (admin only)'),
        ]),
    ]

    for section_title, commands_list in sections:
        section_text = '\n'.join([f'`{cmd}` — {desc}' for cmd, desc in commands_list])
        embed.add_field(name=section_title, value=section_text, inline=False)

    await interaction.response.send_message(embed=embed)

if __name__ == '__main__':
    init_db()
    bot.run(TOKEN)
