"""Create name-colour roles and post the single colour-picker board, idempotently.

A member's name colour comes from their highest role carrying a non-default colour,
so each config colour maps to one Discord role created with that ``colour=`` set. The
bot posts one board (an embed legending each colour's emoji) and seeds its unicode
reactions; reacting picks a colour (see cogs/colour_roles.py for the exclusive-pick
behaviour). Item roles are created with the default colour, so the two never interfere.

Like role_service/factory_service, this mutates the in-memory StateStore but never
saves it; the caller persists once via StateStore.save() so storage stays the single
writer.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import discord

from leaf_valley.services import role_service

if TYPE_CHECKING:
    from leaf_valley.config.schema import ColourConfig
    from leaf_valley.storage.state_store import StateStore

log = logging.getLogger(__name__)

COLOUR_CREATE_REASON = "Leaf Valley: name-colour role"

DESCRIPTION_HEADER = "React to set your name colour (you can only have one at a time):"


@dataclass
class ColourSyncResult:
    """Per-run summary of what happened to each colour's role, by role name."""

    created: list[str] = field(default_factory=list)
    adopted: list[str] = field(default_factory=list)
    existing: list[str] = field(default_factory=list)
    # Set once the bot is denied create permission; remaining creates are skipped.
    forbidden: bool = False

    @property
    def changed(self) -> bool:
        """True when the state store gained or repointed a role ID (needs saving)."""
        return bool(self.created or self.adopted)


@dataclass
class ColourBoardResult:
    """Per-run summary of posting/refreshing the colour board."""

    posted: bool = False
    refreshed: bool = False
    reactions_added: int = 0
    # Set to the existing board's channel ID when run in a different channel; setup
    # refuses to post a second board and reports this instead.
    channel_conflict: int | None = None
    # Set once Discord denies posting/reacting.
    forbidden: bool = False

    @property
    def changed(self) -> bool:
        """True when the board was posted or refreshed (state needs saving)."""
        return self.posted or self.refreshed


async def create_missing_colour_roles(
    guild: discord.Guild,
    config: ColourConfig,
    store: StateStore,
) -> ColourSyncResult:
    """Ensure every config colour has a coloured role in ``guild`` recorded in ``store``.

    Idempotent: re-running only creates/adopts what is missing. Colour roles are made
    non-mentionable (a colour is never pinged). Aborts further creations the first time
    Discord denies role creation, which always means the bot lacks Manage Roles.
    """
    result = ColourSyncResult()

    roles_by_id = {role.id: role for role in guild.roles}
    roles_by_name: dict[str, discord.Role] = {}
    for role in guild.roles:
        roles_by_name.setdefault(role.name, role)

    for colour in config.colours:
        existing_id = store.get_colour_role_id(guild.id, colour.key)
        if existing_id is not None and existing_id in roles_by_id:
            result.existing.append(colour.role_name)
            continue

        adopted = roles_by_name.get(colour.role_name)
        if adopted is not None:
            store.set_colour_role_id(guild.id, colour.key, adopted.id)
            result.adopted.append(colour.role_name)
            continue

        if result.forbidden:
            continue

        try:
            role = await guild.create_role(
                name=colour.role_name,
                colour=discord.Colour(colour.colour),
                mentionable=False,
                reason=COLOUR_CREATE_REASON,
            )
        except discord.Forbidden:
            log.error(
                "Missing 'Manage Roles' in guild %s; cannot create colour role %r.",
                guild.id,
                colour.role_name,
            )
            result.forbidden = True
            continue

        roles_by_id[role.id] = role
        roles_by_name.setdefault(role.name, role)
        store.set_colour_role_id(guild.id, colour.key, role.id)
        result.created.append(colour.role_name)

    log.info(
        "Colour role sync in guild %s: created=%d adopted=%d existing=%d.",
        guild.id,
        len(result.created),
        len(result.adopted),
        len(result.existing),
    )
    return result


async def setup_colour_board(
    guild: discord.Guild,
    channel: discord.abc.Messageable,
    config: ColourConfig,
    store: StateStore,
) -> ColourBoardResult:
    """Post or refresh the single colour board in ``channel`` and seed its reactions.

    Enforces one board per guild: if a board already exists in a different channel,
    returns early with ``channel_conflict`` set. Idempotent — an existing message is
    edited in place and its reactions re-seeded rather than reposted.
    """
    result = ColourBoardResult()

    existing_channel = store.get_colour_channel_id(guild.id)
    if existing_channel is not None and existing_channel != channel.id:
        result.channel_conflict = existing_channel
        return result

    if existing_channel is None:
        store.set_colour_channel_id(guild.id, channel.id)

    embed = build_embed(config)
    try:
        message = await _upsert_message(channel, embed, store, guild.id, result)
        result.reactions_added = await seed_colour_reactions(message, config)
    except discord.Forbidden:
        log.error(
            "Missing permission to post or react in channel %s; "
            "grant View Channel, Send Messages and Add Reactions.",
            getattr(channel, "id", "?"),
        )
        result.forbidden = True

    if result.changed:
        log.info(
            "Colour board in guild %s channel %s: posted=%s refreshed=%s reactions=%d.",
            guild.id,
            getattr(channel, "id", "?"),
            result.posted,
            result.refreshed,
            result.reactions_added,
        )
    return result


def build_embed(config: ColourConfig) -> discord.Embed:
    """Build the colour board embed: a header plus one legend line per colour."""
    lines = [DESCRIPTION_HEADER, ""]
    for colour in config.colours:
        lines.append(f"{colour.emoji} — {colour.role_name}")
    return discord.Embed(title="Name colours", description="\n".join(lines))


async def seed_colour_reactions(message: discord.Message, config: ColourConfig) -> int:
    """Add each colour's unicode emoji to ``message`` as a reaction; return the count."""
    added = 0
    for colour in config.colours:
        await message.add_reaction(colour.emoji)
        added += 1
    return added


async def apply_exclusive_colour(
    member: discord.Member,
    message: discord.Message,
    chosen_role_id: int,
    others: dict[int, str],
) -> None:
    """Give ``member`` the chosen colour and drop every other one they hold.

    ``others`` maps each other colour role ID the member currently holds to its board
    emoji. Removing a member's reaction fires on_raw_reaction_remove, which strips the
    matching role — already gone here, so it's a harmless idempotent no-op.
    """
    await role_service.assign_role(member, chosen_role_id)
    for role_id, emoji in others.items():
        await role_service.remove_role(member, role_id)
        try:
            await message.remove_reaction(emoji, member)
        except (discord.Forbidden, discord.NotFound):
            log.warning(
                "Could not clear old colour reaction %s for member %s in guild %s.",
                emoji,
                member.id,
                member.guild.id,
            )


async def _upsert_message(
    channel: discord.abc.Messageable,
    embed: discord.Embed,
    store: StateStore,
    guild_id: int,
    result: ColourBoardResult,
) -> discord.Message:
    """Edit the board's existing message if it's still there, else post a new one."""
    existing_id = store.get_colour_message_id(guild_id)
    if existing_id is not None:
        try:
            message = await channel.fetch_message(existing_id)
        except discord.NotFound:
            pass  # recorded message was deleted; fall through and repost
        else:
            await message.edit(embed=embed)
            result.refreshed = True
            return message

    message = await channel.send(embed=embed)
    store.set_colour_message_id(guild_id, message.id)
    result.posted = True
    return message
