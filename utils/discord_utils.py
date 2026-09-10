"""Small helpers shared by the command modules."""

from __future__ import annotations

import discord

EMBED_FIELD_LIMIT = 25

GREEN = discord.Color.green()
RED = discord.Color.red()
BLURPLE = discord.Color.blurple()
GREY = discord.Color.greyple()

MENTIONS_ONLY_USERS = discord.AllowedMentions(users=True, roles=False, everyone=False)


def join_words(items: list[str]) -> str:
    """Join a short list for prose: 'a', 'a and b', 'a, b and c'."""
    if len(items) < 3:
        return ' and '.join(items)
    return f'{", ".join(items[:-1])} and {items[-1]}'


def notice(description: str, color: discord.Color) -> discord.Embed:
    return discord.Embed(description=description, color=color)


async def send(
    interaction: discord.Interaction,
    description: str,
    *,
    color: discord.Color = GREEN,
    ephemeral: bool = False,
) -> None:
    """Reply with a single-line embed, using a follow-up if already responded."""
    embed = notice(description, color)
    if interaction.response.is_done():
        await interaction.followup.send(embed=embed, ephemeral=ephemeral)
    else:
        await interaction.response.send_message(embed=embed, ephemeral=ephemeral)


async def send_embed(
    interaction: discord.Interaction,
    embed: discord.Embed,
    *,
    file: discord.File | None = None,
    ephemeral: bool = False,
) -> None:
    """Reply with an embed, including an attached file only when there is one."""
    extra = {'file': file} if file is not None else {}
    if interaction.response.is_done():
        await interaction.followup.send(embed=embed, ephemeral=ephemeral, **extra)
    else:
        await interaction.response.send_message(embed=embed, ephemeral=ephemeral, **extra)


async def fail(interaction: discord.Interaction, description: str) -> None:
    """Reply with an ephemeral error embed."""
    await send(interaction, description, color=RED, ephemeral=True)


async def resolve_user_display(
    client: discord.Client, user_id: int, guild: discord.Guild | None = None
) -> str:
    """Render a user as a clickable mention, falling back to a readable @name."""
    if guild is not None:
        member = guild.get_member(user_id)
        if member:
            return member.mention
    try:
        user = await client.fetch_user(user_id)
    except (discord.NotFound, discord.HTTPException):
        return f'User {user_id}'
    return f'@{user.global_name or user.name}'


def paginate_fields(
    fields: list[tuple[str, str]], build_embed, per_page: int = EMBED_FIELD_LIMIT
) -> list[discord.Embed]:
    """Split (name, value) pairs across embeds, since one embed holds 25 fields."""
    pages = [fields[start:start + per_page] for start in range(0, len(fields), per_page)] or [[]]
    embeds = []
    for index, page in enumerate(pages, start=1):
        embed = build_embed()
        if len(pages) > 1:
            embed.set_footer(text=f'Page {index} of {len(pages)}')
        for name, value in page:
            embed.add_field(name=name, value=value, inline=False)
        embeds.append(embed)
    return embeds
