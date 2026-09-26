"""/stats and /leaderboard, plus the heatmap and streak calculations behind them."""

from __future__ import annotations

from datetime import date, datetime, timedelta

import discord
from discord import app_commands

from utils import activities, db
from utils import discord_utils as ui
from utils import timezones
from utils.session_utils import session_duration_seconds_sql
from utils.time_utils import format_seconds, format_time

HEATMAP_WEEKS = 12
WEEKDAY_NAMES = ('Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday')

EMPTY_SQUARE = '⬜'
# (exclusive upper bound in hours, square) — the first match wins.
HEATMAP_SCALE = ((2, '🟩'), (4, '🟨'), (6, '🟧'), (8, '🟥'))
HEATMAP_OVERFLOW = '🟪'
HEATMAP_LEGEND = '⬜ 0h  🟩 <2h  🟨 2-4h\n🟧 4-6h  🟥 6-8h  🟪 8h+'


def _square(hours: float) -> str:
    if hours <= 0:
        return EMPTY_SQUARE
    return next((symbol for limit, symbol in HEATMAP_SCALE if hours < limit), HEATMAP_OVERFLOW)


@app_commands.command(name='stats', description='View all your stats with 12-week activity')
@app_commands.describe(user='Optional: mention a member to view their stats')
async def stats(interaction: discord.Interaction, user: discord.User | None = None) -> None:
    target = user or interaction.user
    now = datetime.now()
    # Day boundaries follow the tracked user's timezone so the numbers mean the
    # same thing no matter who is looking.
    today = timezones.today_for_user(target.id)

    with db.db() as conn:
        embeds = [_totals_embed(conn, interaction, target, now, today)]
        daily_stats = _daily_totals(conn, target.id, now, today - timedelta(weeks=HEATMAP_WEEKS))

        if daily_stats:
            embeds.append(
                discord.Embed(
                    title=f'{HEATMAP_WEEKS}-Week Activity',
                    description=generate_heatmap(daily_stats, today - timedelta(weeks=HEATMAP_WEEKS), today),
                    color=ui.GREEN,
                )
            )
            embeds.append(_metrics_embed(conn, target.id, now, today, daily_stats))

    embeds[0].insert_field_at(
        0,
        name='User',
        value=await ui.resolve_user_display(interaction.client, target.id, interaction.guild),
        inline=False,
    )
    await interaction.response.send_message(embeds=embeds, allowed_mentions=ui.MENTIONS_ONLY_USERS)


def _totals_embed(conn, interaction, target, now: datetime, today: date) -> discord.Embed:
    periods = (
        ('Today', today),
        ('This Week', today - timedelta(days=today.weekday())),
        ('All-Time', None),
    )

    embed = discord.Embed(title='Time Tracking', color=ui.BLURPLE)
    for label, start in periods:
        rows = conn.execute(
            f'''SELECT activities.name,
                       SUM({session_duration_seconds_sql()}) AS seconds
                FROM sessions
                JOIN activities ON sessions.activity_id = activities.id
                WHERE sessions.user_id = ?{' AND sessions.date >= ?' if start else ''}
                GROUP BY activities.name
                ORDER BY seconds DESC''',
            (now.isoformat(), target.id, *((start.isoformat(),) if start else ())),
        ).fetchall()

        if not rows:
            if label != 'Today':
                embed.add_field(name=label, value='No time tracked', inline=True)
            continue

        total_seconds = sum(row['seconds'] or 0 for row in rows)
        if len(rows) == 1:
            text = format_seconds(rows[0]['seconds'] or 0)
        else:
            text = '\n'.join(f'{row["name"]}: {format_seconds(row["seconds"] or 0)}' for row in rows)
            text += f'\n\n**Total: {format_seconds(total_seconds)}**'
        embed.add_field(name=label, value=text, inline=True)

    return embed


def _daily_totals(conn, user_id: int, now: datetime, since: date) -> dict[str, float]:
    rows = conn.execute(
        f'''SELECT date AS day, SUM({session_duration_seconds_sql()}) AS seconds
            FROM sessions
            WHERE user_id = ? AND date >= ?
            GROUP BY date
            ORDER BY day''',
        (now.isoformat(), user_id, since.isoformat()),
    ).fetchall()
    return {row['day']: (row['seconds'] or 0) / 3600 for row in rows}


def _metrics_embed(conn, user_id: int, now: datetime, today: date, daily_stats: dict) -> discord.Embed:
    favorite = conn.execute(
        f'''SELECT activities.name, SUM({session_duration_seconds_sql()}) AS seconds
            FROM sessions
            JOIN activities ON sessions.activity_id = activities.id
            WHERE sessions.user_id = ?
            GROUP BY activities.name
            ORDER BY seconds DESC, activities.name ASC
            LIMIT 1''',
        (now.isoformat(), user_id),
    ).fetchone()

    busiest = conn.execute(
        f'''SELECT strftime('%w', date) AS day_of_week, SUM({session_duration_seconds_sql()}) AS seconds
            FROM sessions
            WHERE user_id = ?
            GROUP BY day_of_week
            ORDER BY seconds DESC
            LIMIT 1''',
        (now.isoformat(), user_id),
    ).fetchone()

    embed = discord.Embed(title='Key Metrics', color=discord.Color.orange())
    embed.add_field(name='Streak', value=f'{calculate_streak(daily_stats, today)} days', inline=True)
    embed.add_field(name='Max Day', value=format_time(max(daily_stats.values())), inline=True)
    embed.add_field(
        name='Daily Avg',
        value=format_time(sum(daily_stats.values()) / len(daily_stats)),
        inline=True,
    )
    if busiest:
        day_name = WEEKDAY_NAMES[int(busiest['day_of_week'])]
        embed.add_field(
            name='Most Active',
            value=f'{day_name} ({format_seconds(busiest["seconds"] or 0)})',
            inline=True,
        )
    if favorite:
        embed.add_field(
            name='Favorite Activity',
            value=f'**{favorite["name"]}** ({format_seconds(favorite["seconds"] or 0)})',
            inline=True,
        )
    return embed


def generate_heatmap(daily_stats: dict, start_date: date, end_date: date) -> str:
    """Render tracked hours as one row of coloured squares per calendar week."""
    weeks: list[tuple[date, str]] = []
    week_start = start_date
    current_week = [EMPTY_SQUARE] * start_date.weekday()

    current = start_date
    while current <= end_date:
        current_week.append(_square(daily_stats.get(current.isoformat(), 0)))
        if len(current_week) == 7:
            weeks.append((week_start, ''.join(current_week)))
            week_start = current + timedelta(days=1)
            current_week = []
        current += timedelta(days=1)

    if current_week:
        weeks.append((week_start, ''.join(current_week)))

    rows = [
        (_week_label(start, start + timedelta(days=len(cells) - 1)), cells)
        for start, cells in weeks
        if any(cell != EMPTY_SQUARE for cell in cells)
    ]
    if not rows:
        return f'```\nNo tracked time in this period.\n{HEATMAP_LEGEND}\n```'

    width = max(len(label) for label, _ in rows)
    body = '\n'.join(f'{label:<{width}} | {cells}' for label, cells in rows)
    return f'```\n{body}\n{HEATMAP_LEGEND}\n```'


def _week_label(start: date, end: date) -> str:
    if start.month == end.month:
        return f'{start:%b}. {start.day}-{end.day}'
    return f'{start:%b}. {start.day}-{end:%b}. {end.day}'


def calculate_streak(daily_stats: dict, today: date) -> int:
    """Count consecutive tracked days ending today; zero if today is untracked."""
    streak = 0
    current = today
    while current.isoformat() in daily_stats:
        streak += 1
        current -= timedelta(days=1)
    return streak


@app_commands.command(name='leaderboard', description='View leaderboard for an activity')
@app_commands.describe(activity_name='Optional: activity to rank; defaults to the server default activity')
@app_commands.autocomplete(activity_name=activities.autocomplete)
async def leaderboard(interaction: discord.Interaction, activity_name: str | None = None) -> None:
    with db.db() as conn:
        try:
            activity = activities.resolve(conn, activity_name, interaction.guild)
        except activities.ActivityUnavailable as error:
            await ui.fail(interaction, str(error))
            return

        rows = conn.execute(
            f'''SELECT user_id, SUM({session_duration_seconds_sql()}) AS seconds
                FROM sessions
                WHERE activity_id = ?
                GROUP BY user_id
                ORDER BY seconds DESC
                LIMIT 10''',
            (datetime.now().isoformat(), activity['id']),
        ).fetchall()

    if not rows:
        await ui.send(interaction, f'No one has tracked time for **{activity["name"]}** yet!', color=ui.GREY)
        return

    medals = ('1st', '2nd', '3rd')
    embed = discord.Embed(title=f'{activity["name"]} - Leaderboard', color=discord.Color.gold())
    for index, row in enumerate(rows):
        display = await ui.resolve_user_display(interaction.client, row['user_id'], interaction.guild)
        rank = medals[index] if index < len(medals) else f'{index + 1}.'
        embed.add_field(name=rank, value=f'{display}\n{format_seconds(row["seconds"] or 0)}', inline=False)

    await interaction.response.send_message(embed=embed, allowed_mentions=ui.MENTIONS_ONLY_USERS)


def register(tree: app_commands.CommandTree) -> None:
    tree.add_command(stats)
    tree.add_command(leaderboard)
