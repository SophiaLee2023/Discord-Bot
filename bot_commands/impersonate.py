"""/impersonate — put a member's avatar, nickname, and colour on the bot."""

from __future__ import annotations

import discord
from discord import app_commands

from utils import db
from utils import discord_utils as ui
from utils import permissions

AVATAR_SIZE = 256
NICKNAME_LIMIT = 32

# The bot keeps one role of its own for colour. It is recoloured on each call
# rather than borrowing a member's role, so nobody else's role is touched and
# no permissions tag along.
ROLE_NAME = 'Flair'

# Outcome of each of the three steps.
CHANGED = 'changed'
UNCHANGED = 'unchanged'

# Discord rate limits bot avatar changes far more tightly than anything else the
# bot does — roughly a couple of changes per hour — so an unnecessary upload is
# expensive. Every step below checks before it writes.
RATE_LIMIT_HINT = (
    'Discord limits how often a bot may change its avatar (roughly twice an hour). '
    'Wait a while and try again.'
)


def display_colour(user: discord.Member) -> discord.Colour:
    """The colour Discord shows for a member: their highest role that has one."""
    for role in reversed(user.roles):
        if role.colour.value:
            return role.colour
    return discord.Colour.default()


@app_commands.command(
    name='impersonate', description="Copy a member's avatar, nickname, and colour onto the bot"
)
@app_commands.describe(user='Member whose identity the bot should copy')
async def impersonate(interaction: discord.Interaction, user: discord.Member) -> None:
    if interaction.guild is None:
        await ui.fail(interaction, 'This command only works in a server.')
        return
    if not permissions.is_admin(interaction):
        await ui.fail(interaction, 'You need to be an admin to use this command!')
        return

    await interaction.response.defer()

    failures: list[str] = []
    reason = f'/impersonate by {interaction.user}'
    colour = display_colour(user)

    results = {
        'avatar': await _apply_avatar(interaction.client, user, failures),
        'nickname': await _apply_nickname(interaction.guild, user, reason, failures),
        'colour': await _apply_colour(interaction.guild, colour, reason, failures),
    }
    changed = [name for name, result in results.items() if result == CHANGED]
    unchanged = [name for name, result in results.items() if result == UNCHANGED]

    if not changed and not unchanged:
        await ui.fail(interaction, '\n'.join(failures))
        return

    embed = ui.notice(_summary(user, changed, unchanged), ui.GREEN)
    embed.colour = colour if colour.value else ui.GREEN
    embed.set_footer(text='Discord still shows the BOT tag; the avatar change applies everywhere.')
    if failures:
        embed.add_field(name='Not changed', value='\n'.join(failures), inline=False)
    await interaction.followup.send(embed=embed, allowed_mentions=ui.MENTIONS_ONLY_USERS)


def _summary(user: discord.Member, changed: list[str], unchanged: list[str]) -> str:
    if not changed:
        return (
            f'Already impersonating {user.mention} — {ui.join_words(unchanged)} '
            'all match, nothing to update.'
        )
    text = f'Now impersonating {user.mention} — copied their {ui.join_words(changed)}'
    if unchanged:
        text += f'; {ui.join_words(unchanged)} already matched'
    return text + '.'


async def _apply_avatar(
    client: discord.Client, user: discord.Member, failures: list[str]
) -> str | None:
    """Upload the member's avatar, unless the bot is already wearing exactly it.

    The bot's own avatar hash differs from the member's even for identical
    images, so the source hash is recorded instead. The hash the upload produced
    is recorded too, which catches the avatar being changed elsewhere.
    """
    source_key = user.display_avatar.key
    state = db.get_bot_avatar_state()
    current_key = client.user.avatar.key if client.user.avatar else None

    if (
        state is not None
        and state['source_user_id'] == user.id
        and state['source_key'] == source_key
        and state['applied_key'] == current_key
    ):
        return UNCHANGED

    try:
        # Force a static PNG: bots cannot wear animated avatars.
        payload = await user.display_avatar.with_format('png').with_size(AVATAR_SIZE).read()
    except discord.HTTPException:
        failures.append(f"Avatar: could not download {user.display_name}'s avatar.")
        return None

    try:
        await client.user.edit(avatar=payload)
    except discord.HTTPException as error:
        failures.append(RATE_LIMIT_HINT if error.status == 429 else f'Avatar: {error.text or error}')
        return None

    applied = client.user.avatar.key if client.user.avatar else None
    db.set_bot_avatar_state(user.id, source_key, applied)
    return CHANGED


async def _apply_nickname(
    guild: discord.Guild, user: discord.Member, reason: str, failures: list[str]
) -> str | None:
    nickname = user.display_name[:NICKNAME_LIMIT]
    if guild.me.nick == nickname:
        return UNCHANGED

    try:
        await guild.me.edit(nick=nickname, reason=reason)
    except discord.Forbidden:
        failures.append('Nickname: the bot needs the **Change Nickname** permission in this server.')
        return None
    except discord.HTTPException as error:
        failures.append(f'Nickname: {error.text or error}')
        return None
    return CHANGED


async def _apply_colour(
    guild: discord.Guild, colour: discord.Colour, reason: str, failures: list[str]
) -> str | None:
    """Point the bot's own Flair role at `colour`, creating it the first time."""
    role = _existing_flair_role(guild)
    result = UNCHANGED

    if role is None:
        try:
            role = await guild.create_role(name=ROLE_NAME, colour=colour, reason=reason)
            result = CHANGED
        except discord.Forbidden:
            failures.append('Colour: the bot needs the **Manage Roles** permission in this server.')
            return None
        except discord.HTTPException as error:
            failures.append(f'Colour: {error.text or error}')
            return None
    elif role.colour != colour:
        try:
            await role.edit(colour=colour, reason=reason)
            result = CHANGED
        except discord.Forbidden:
            failures.append(f'Colour: the bot cannot edit **{ROLE_NAME}** — move its own role above it.')
            return None
        except discord.HTTPException as error:
            failures.append(f'Colour: {error.text or error}')
            return None

    db.set_colour_role_id(guild.id, role.id)

    if role not in guild.me.roles:
        try:
            await guild.me.add_roles(role, reason=reason)
            result = CHANGED
        except discord.HTTPException as error:
            failures.append(f'Colour: could not give the bot **{ROLE_NAME}** — {error.text or error}')
            return None

    _warn_if_outranked(guild, role, failures)
    return result


def _existing_flair_role(guild: discord.Guild) -> discord.Role | None:
    """The bot's Flair role, by stored id or by name after a database reset.

    The name is checked even when the id matches, so a stale row can never make
    the bot recolour a role it does not own.
    """
    role_id = db.get_colour_role_id(guild.id)
    if role_id is not None:
        role = guild.get_role(role_id)
        if role is not None and role.name == ROLE_NAME:
            return role
        db.clear_colour_role_id(guild.id)
    return discord.utils.get(guild.roles, name=ROLE_NAME)


def _warn_if_outranked(guild: discord.Guild, role: discord.Role, failures: list[str]) -> None:
    """Discord shows the highest coloured role, so anything above Flair wins.

    The fix is to clear the colour on that higher role, not to move Flair above
    it: a role above the bot's own is one the bot is no longer allowed to edit,
    which would freeze Flair on whatever colour it happened to have.
    """
    higher = max(
        (other for other in guild.me.roles if other.colour.value and other.position > role.position),
        key=lambda other: other.position,
        default=None,
    )
    if higher is not None:
        failures.append(
            f'Colour: **{higher.name}** sits above **{ROLE_NAME}** on the bot and has a colour, so '
            f"its colour shows instead. Clear that role's colour in Server Settings → Roles."
        )


def register(tree: app_commands.CommandTree) -> None:
    tree.add_command(impersonate)
