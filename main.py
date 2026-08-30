import os
import sqlite3
from datetime import datetime, timedelta
from dotenv import load_dotenv
import discord
from discord.ext import commands

load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='.', intents=intents)

DB_PATH = 'time_tracker.db'

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS activities (
            id INTEGER PRIMARY KEY,
            name TEXT UNIQUE NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT role_id FROM admin_roles WHERE guild_id = ?', (ctx.guild.id,))
    admin_role_ids = [row['role_id'] for row in cursor.fetchall()]
    conn.close()

    if not admin_role_ids:
        return False

    user_role_ids = [role.id for role in ctx.author.roles]
    return any(role_id in user_role_ids for role_id in admin_role_ids)

@bot.event
async def on_ready():
    print(f'{bot.user} has connected to Discord!')
    print('Bot is ready.')

@bot.before_invoke
async def before_invoke(ctx):
    if ctx.guild:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('SELECT channel_id FROM allowed_channels WHERE guild_id = ?', (ctx.guild.id,))
        allowed_channels = [row['channel_id'] for row in cursor.fetchall()]
        conn.close()

        if allowed_channels and ctx.channel.id not in allowed_channels:
            try:
                await ctx.message.delete()
            except:
                pass
            return

    try:
        await ctx.message.delete()
    except:
        pass

@bot.command(name='activitycreate', help='Create a new activity/commitment to track')
async def activitycreate(ctx, *, name: str):
    if not is_admin(ctx):
        await ctx.send('❌ You need to be an admin to use this command!')
        return

    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('INSERT INTO activities (name) VALUES (?)', (name,))
        conn.commit()
        conn.close()
        await ctx.send(f'✅ Activity "{name}" created!')
    except sqlite3.IntegrityError:
        await ctx.send(f'❌ Activity "{name}" already exists!')
    except Exception as e:
        await ctx.send(f'❌ Error: {str(e)}')

@bot.command(name='activitylist', help='List all activities')
async def activitylist(ctx):
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('SELECT id, name FROM activities ORDER BY name')
        activities = cursor.fetchall()
        conn.close()

        if not activities:
            await ctx.send('No activities found. Create one with `.activitycreate`')
            return

        embed = discord.Embed(title='📋 Activities', color=discord.Color.blue())
        for activity in activities:
            embed.add_field(name=activity['name'], value='', inline=False)
        await ctx.send(embed=embed)
    except Exception as e:
        await ctx.send(f'❌ Error: {str(e)}')

@bot.command(name='clockin', help='Clock in to an activity')
async def clockin(ctx, *, activity_name: str):
    try:
        conn = get_db()
        cursor = conn.cursor()

        cursor.execute('SELECT id, name FROM activities WHERE LOWER(name) = LOWER(?)', (activity_name,))
        activity = cursor.fetchone()
        if not activity:
            await ctx.send(f'❌ Activity "{activity_name}" not found!')
            conn.close()
            return

        cursor.execute('''
            SELECT id FROM time_entries
            WHERE user_id = ? AND clock_out IS NULL
        ''', (ctx.author.id,))
        active = cursor.fetchone()

        if active:
            await ctx.send('❌ You are already clocked in! Clock out first with `.clockout`')
            conn.close()
            return

        cursor.execute('''
            INSERT INTO time_entries (user_id, activity_id, clock_in)
            VALUES (?, ?, ?)
        ''', (ctx.author.id, activity['id'], datetime.now()))
        conn.commit()
        conn.close()

        await ctx.send(f'✅ Clocked in to **{activity["name"]}**')
    except Exception as e:
        await ctx.send(f'❌ Error: {str(e)}')

@bot.command(name='clockout', help='Clock out of current activity')
async def clockout(ctx):
    try:
        conn = get_db()
        cursor = conn.cursor()

        cursor.execute('''
            SELECT time_entries.id, activities.name, time_entries.clock_in
            FROM time_entries
            JOIN activities ON time_entries.activity_id = activities.id
            WHERE time_entries.user_id = ? AND time_entries.clock_out IS NULL
        ''', (ctx.author.id,))
        active = cursor.fetchone()

        if not active:
            await ctx.send('❌ You are not clocked in!')
            conn.close()
            return

        cursor.execute('''
            UPDATE time_entries
            SET clock_out = ?
            WHERE id = ?
        ''', (datetime.now(), active['id']))
        conn.commit()
        conn.close()

        duration = datetime.now() - datetime.fromisoformat(active['clock_in'])
        hours = duration.total_seconds() / 3600
        await ctx.send(f'✅ Clocked out of **{active["name"]}** ({hours:.2f}h)')
    except Exception as e:
        await ctx.send(f'❌ Error: {str(e)}')

@bot.command(name='stats', help='View all your stats with heatmap')
async def stats(ctx, user: discord.User = None):
    try:
        if user is None:
            user = ctx.author

        conn = get_db()
        cursor = conn.cursor()
        now = datetime.now()

        embed = discord.Embed(title=f"📊 {user.name}'s Stats", color=discord.Color.green())

        periods = [
            ('today', now.replace(hour=0, minute=0, second=0, microsecond=0), '📅 Today'),
            ('week', now - timedelta(days=now.weekday()), '📆 This Week'),
            ('all', datetime.min, '⏳ All-Time'),
        ]

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
                embed.add_field(name=label, value='No time tracked', inline=False)
            else:
                total_hours = 0
                stats_text = ''
                for row in stats_data:
                    hours = row['hours'] or 0
                    total_hours += hours
                    stats_text += f"{row['name']}: {hours:.2f}h\n"
                stats_text += f"**Total: {total_hours:.2f}h**"
                embed.add_field(name=label, value=stats_text, inline=False)

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
            embed.add_field(name='📅 Heatmap (Last 12 Weeks)', value=heatmap, inline=False)

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

            most_in_day = max(daily_stats.values())
            total_hours = sum(daily_stats.values())
            avg_per_day = total_hours / len(daily_stats) if daily_stats else 0
            streak = calculate_streak(daily_stats)

            stats_summary = f'🔥 Streak: {streak} days | 📊 Max Day: {most_in_day:.1f}h | 📈 Avg: {avg_per_day:.1f}h/day | 🌟 Most Active: {most_active_day}'
            embed.add_field(name='📈 Key Metrics', value=stats_summary, inline=False)

        conn.close()
        await ctx.send(embed=embed)
    except Exception as e:
        await ctx.send(f'❌ Error: {str(e)}')

@bot.command(name='status', help='View your current status')
async def status(ctx):
    try:
        conn = get_db()
        cursor = conn.cursor()

        cursor.execute('''
            SELECT activities.name, time_entries.clock_in
            FROM time_entries
            JOIN activities ON time_entries.activity_id = activities.id
            WHERE time_entries.user_id = ? AND time_entries.clock_out IS NULL
        ''', (ctx.author.id,))

        active = cursor.fetchone()
        conn.close()

        if not active:
            await ctx.send(f'You are currently **not clocked in**')
            return

        clock_in = datetime.fromisoformat(active['clock_in'])
        duration = datetime.now() - clock_in
        hours = duration.total_seconds() / 3600

        embed = discord.Embed(title='⏱️ Current Status', color=discord.Color.yellow())
        embed.add_field(name='Activity', value=active['name'], inline=False)
        embed.add_field(name='Duration', value=f'{hours:.2f}h', inline=False)
        await ctx.send(embed=embed)
    except Exception as e:
        await ctx.send(f'❌ Error: {str(e)}')

def generate_heatmap(daily_stats, start_date):
    now = datetime.now()
    weeks = []
    current_week = []

    start_weekday = start_date.weekday()
    for i in range(start_weekday):
        current_week.append('  ')

    current_date = start_date
    while current_date <= now:
        day_str = current_date.strftime('%Y-%m-%d')
        hours = daily_stats.get(day_str, 0)

        if hours == 0:
            emoji = '⬜'
        elif hours < 2:
            emoji = '🟩'
        elif hours < 4:
            emoji = '🟩'
        elif hours < 6:
            emoji = '🟨'
        elif hours < 8:
            emoji = '🟧'
        else:
            emoji = '🟥'

        current_week.append(emoji)

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

@bot.command(name='leaderboard', help='View leaderboard for an activity')
async def leaderboard(ctx, *, activity_name: str):
    try:
        conn = get_db()
        cursor = conn.cursor()

        cursor.execute('SELECT id, name FROM activities WHERE LOWER(name) = LOWER(?)', (activity_name,))
        activity = cursor.fetchone()
        if not activity:
            await ctx.send(f'❌ Activity "{activity_name}" not found!')
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
            await ctx.send(f'No one has tracked time for **{activity["name"]}** yet!')
            return

        embed = discord.Embed(title=f'🏆 {activity["name"]} Leaderboard', color=discord.Color.gold())
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
            embed.add_field(name=f'{medal} {username}', value=f'{hours:.2f}h', inline=False)

        await ctx.send(embed=embed)
    except Exception as e:
        await ctx.send(f'❌ Error: {str(e)}')

@bot.group(name='adminrole', help='Manage admin roles')
async def adminrole(ctx):
    if ctx.invoked_subcommand is None:
        await ctx.send('Use `.adminrole add <role>`, `.adminrole remove <role>`, or `.adminrole list`')

@adminrole.command(name='add', help='Add an admin role')
async def adminrole_add(ctx, *, role_name: str):
    if not ctx.author.guild_permissions.administrator:
        await ctx.send('❌ Only server admins can manage admin roles!')
        return

    role = discord.utils.find(lambda r: r.name.lower() == role_name.lower(), ctx.guild.roles)
    if not role:
        await ctx.send(f'❌ Role "{role_name}" not found!')
        return

    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('INSERT INTO admin_roles (guild_id, role_id) VALUES (?, ?)', (ctx.guild.id, role.id))
        conn.commit()
        conn.close()
        await ctx.send(f'✅ Added `{role.name}` as an admin role!')
    except sqlite3.IntegrityError:
        await ctx.send(f'❌ `{role.name}` is already an admin role!')
    except Exception as e:
        await ctx.send(f'❌ Error: {str(e)}')

@adminrole.command(name='remove', help='Remove an admin role')
async def adminrole_remove(ctx, *, role_name: str):
    if not ctx.author.guild_permissions.administrator:
        await ctx.send('❌ Only server admins can manage admin roles!')
        return

    role = discord.utils.find(lambda r: r.name.lower() == role_name.lower(), ctx.guild.roles)
    if not role:
        await ctx.send(f'❌ Role "{role_name}" not found!')
        return

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('DELETE FROM admin_roles WHERE guild_id = ? AND role_id = ?', (ctx.guild.id, role.id))
    conn.commit()
    deleted = cursor.rowcount
    conn.close()

    if deleted:
        await ctx.send(f'✅ Removed `{role.name}` from admin roles!')
    else:
        await ctx.send(f'❌ `{role.name}` is not an admin role!')

@adminrole.command(name='list', help='List all admin roles')
async def adminrole_list(ctx):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT role_id FROM admin_roles WHERE guild_id = ?', (ctx.guild.id,))
    role_ids = [row['role_id'] for row in cursor.fetchall()]
    conn.close()

    if not role_ids:
        await ctx.send('No admin roles configured yet!')
        return

    embed = discord.Embed(title='👮 Admin Roles', color=discord.Color.blurple())
    for role_id in role_ids:
        role = ctx.guild.get_role(role_id)
        if role:
            embed.add_field(name=role.name, value='', inline=False)
        else:
            embed.add_field(name=f'Unknown Role ({role_id})', value='*Role was deleted*', inline=False)

    await ctx.send(embed=embed)

@bot.group(name='channel', help='Manage bot channels')
async def channel(ctx):
    if ctx.invoked_subcommand is None:
        await ctx.send('Use `.channel add <channel>`, `.channel remove <channel>`, or `.channel list`')

@channel.command(name='add', help='Add a channel where the bot can work')
async def channel_add(ctx, *, channel_name: str):
    if not ctx.author.guild_permissions.administrator:
        await ctx.send('❌ Only server admins can manage bot channels!')
        return

    channel = discord.utils.find(lambda c: c.name.lower() == channel_name.lower() and isinstance(c, discord.TextChannel), ctx.guild.channels)
    if not channel:
        await ctx.send(f'❌ Channel "{channel_name}" not found!')
        return

    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('INSERT INTO allowed_channels (guild_id, channel_id) VALUES (?, ?)', (ctx.guild.id, channel.id))
        conn.commit()
        conn.close()
        await ctx.send(f'✅ Bot is now allowed in #{channel.name}')
    except sqlite3.IntegrityError:
        await ctx.send(f'❌ Bot is already allowed in #{channel.name}!')
    except Exception as e:
        await ctx.send(f'❌ Error: {str(e)}')

@channel.command(name='remove', help='Remove a channel where the bot can work')
async def channel_remove(ctx, *, channel_name: str):
    if not ctx.author.guild_permissions.administrator:
        await ctx.send('❌ Only server admins can manage bot channels!')
        return

    channel = discord.utils.find(lambda c: c.name.lower() == channel_name.lower() and isinstance(c, discord.TextChannel), ctx.guild.channels)
    if not channel:
        await ctx.send(f'❌ Channel "{channel_name}" not found!')
        return

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('DELETE FROM allowed_channels WHERE guild_id = ? AND channel_id = ?', (ctx.guild.id, channel.id))
    conn.commit()
    deleted = cursor.rowcount
    conn.close()

    if deleted:
        await ctx.send(f'✅ Bot is no longer allowed in #{channel.name}')
    else:
        await ctx.send(f'❌ Bot is not restricted to #{channel.name}')

@channel.command(name='list', help='List all allowed channels')
async def channel_list(ctx):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT channel_id FROM allowed_channels WHERE guild_id = ?', (ctx.guild.id,))
    channel_ids = [row['channel_id'] for row in cursor.fetchall()]
    conn.close()

    if not channel_ids:
        await ctx.send('No channel restrictions set! Bot works in all channels.')
        return

    embed = discord.Embed(title='📍 Allowed Bot Channels', color=discord.Color.blurple())
    for channel_id in channel_ids:
        channel = ctx.guild.get_channel(channel_id)
        if channel:
            embed.add_field(name=f'#{channel.name}', value='', inline=False)
        else:
            embed.add_field(name=f'Unknown Channel ({channel_id})', value='*Channel was deleted*', inline=False)

    await ctx.send(embed=embed)

@bot.command(name='say', help='Make the bot say something')
async def say(ctx, *, message: str):
    await ctx.send(message)

@bot.command(name='help', help='Show all time tracking commands')
async def help(ctx):
    embed = discord.Embed(title='⏰ Time Tracking Bot Commands', color=discord.Color.purple())
    commands_info = [
        ('.activitycreate <name>', 'Create a new activity to track'),
        ('.activitylist', 'List all available activities'),
        ('.clockin <activity_name>', 'Clock in to an activity'),
        ('.clockout', 'Clock out of your current activity'),
        ('.status', 'View your current status'),
        ('.stats', 'View all your stats with heatmap and metrics'),
        ('.leaderboard <activity_name>', 'View leaderboard for an activity'),
        ('.adminrole add <role>', 'Add an admin role (server admin only)'),
        ('.adminrole remove <role>', 'Remove an admin role (server admin only)'),
        ('.adminrole list', 'List all admin roles'),
    ]
    for cmd, desc in commands_info:
        embed.add_field(name=cmd, value=desc, inline=False)
    await ctx.send(embed=embed)

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        command_name = ctx.message.content[1:].split()[0] if ctx.message.content.startswith('.') else None
        if command_name:
            conn = get_db()
            cursor = conn.cursor()
            cursor.execute('SELECT id, name FROM activities WHERE LOWER(name) = LOWER(?)', (command_name,))
            activity = cursor.fetchone()
            conn.close()

            if activity:
                cursor = get_db().cursor()
                cursor.execute('''
                    SELECT id FROM time_entries
                    WHERE user_id = ? AND clock_out IS NULL
                ''', (ctx.author.id,))
                active = cursor.fetchone()
                cursor.connection.close()

                if active:
                    await ctx.send('❌ You are already clocked in! Clock out first with `.clockout`')
                    return

                conn = get_db()
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT INTO time_entries (user_id, activity_id, clock_in)
                    VALUES (?, ?, ?)
                ''', (ctx.author.id, activity['id'], datetime.now()))
                conn.commit()
                conn.close()

                await ctx.send(f'✅ Clocked in to **{activity["name"]}**')
                return

        await ctx.send('❌ Command not found! Use `.help` to see all commands.')
    else:
        raise error

if __name__ == '__main__':
    init_db()
    bot.run(TOKEN)
