"""Post factory embeds and seed their item reactions, idempotently.

Each factory in the config maps to one Discord message: an embed titled with the
factory name, optionally showing a picture attached from assets/factories/, whose
body legends each item's emoji. The bot then reacts to that message with every
item's application emoji, turning the message into a reaction-role signup board.

Idempotent: a message already recorded in state is edited in place and its reactions
re-seeded rather than reposted, so re-running never duplicates boards. A recorded ID
whose message has since been deleted is treated as missing and reposted.

Application emojis are resolved by name up front; if any ``:name:`` referenced in the
config has not been seeded yet, setup aborts before posting anything and reports the
missing names, pointing back to scripts/seed_emojis.py.

Like role_service, this mutates the in-memory StateStore but never saves it; the
caller persists once via StateStore.save() so the storage layer stays the single writer.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import discord

if TYPE_CHECKING:
    from leaf_valley.config.schema import Factory, FactoryConfig
    from leaf_valley.storage.state_store import StateStore

log = logging.getLogger(__name__)

DESCRIPTION_HEADER = "React with an emoji to prep that item this week:"


@dataclass
class FactorySetupResult:
    """Per-run summary of what happened to each factory message, by factory name."""

    posted: list[str] = field(default_factory=list)
    refreshed: list[str] = field(default_factory=list)
    reactions_added: int = 0
    # Emoji names referenced in config but not yet uploaded; when non-empty setup
    # aborts before posting anything.
    missing_emojis: list[str] = field(default_factory=list)
    # Set to the existing board's channel ID when setup is run in a different
    # channel; setup refuses to post a second board and reports this instead.
    channel_conflict: int | None = None
    # Set once Discord denies posting/reacting; remaining factories are skipped.
    forbidden: bool = False

    @property
    def aborted(self) -> bool:
        """True when missing emojis prevented any posting."""
        return bool(self.missing_emojis)

    @property
    def changed(self) -> bool:
        """True when a message was posted or refreshed (state needs saving)."""
        return bool(self.posted or self.refreshed)


@dataclass
class TeardownResult:
    """Per-run summary of deleting a guild's factory messages."""

    deleted: int = 0
    # Tracked messages that were already gone (deleted by hand, etc.).
    already_gone: int = 0
    # Set if Discord denies a delete; state is left intact so the admin can retry.
    forbidden: bool = False


@dataclass
class ReactionResetResult:
    """Per-run summary of clearing and re-seeding each factory message's reactions."""

    messages_reset: int = 0
    reactions_added: int = 0
    # Tracked messages that were already gone (deleted by hand, etc.).
    already_gone: int = 0
    # Emoji names referenced in config but no longer uploaded; when non-empty the
    # reset aborts before touching any message.
    missing_emojis: list[str] = field(default_factory=list)
    # Set once Discord denies clearing/reacting; remaining messages are skipped.
    forbidden: bool = False

    @property
    def aborted(self) -> bool:
        """True when missing emojis prevented any reset."""
        return bool(self.missing_emojis)


async def setup_factories(
    guild: discord.Guild,
    channel: discord.abc.Messageable,
    config: FactoryConfig,
    store: StateStore,
    emojis_by_name: dict[str, discord.Emoji],
    factories_dir: Path,
) -> FactorySetupResult:
    """Post or refresh every factory message in ``channel`` and seed its reactions.

    Enforces a single board per guild: if the guild already has a board in a different
    channel, returns early with ``channel_conflict`` set so the caller can tell the
    admin to tear the old one down first. On the first successful run it records
    ``channel`` as the guild's board channel.

    Resolves all ``:name:`` emoji references against ``emojis_by_name`` first; if any
    are missing, returns early without posting so the caller can direct the admin to
    the seed script. Aborts further work (keeping what succeeded) the first time
    Discord denies a post or reaction, which always means a missing permission.
    """
    result = FactorySetupResult()

    existing_channel = store.get_channel_id(guild.id)
    if existing_channel is not None and existing_channel != channel.id:
        result.channel_conflict = existing_channel
        return result

    result.missing_emojis = _find_missing_emojis(config, emojis_by_name)
    if result.missing_emojis:
        return result

    if existing_channel is None:
        store.set_channel_id(guild.id, channel.id)

    for factory in config.factories:
        embed, file = build_embed(factory, emojis_by_name, factories_dir)
        try:
            message = await _upsert_message(
                channel, factory, embed, file, store, guild.id, result
            )
            result.reactions_added += await seed_reactions(
                message, factory, emojis_by_name
            )
        except discord.Forbidden:
            log.error(
                "Missing permission to post or react in channel %s; "
                "grant View Channel, Send Messages and Add Reactions.",
                getattr(channel, "id", "?"),
            )
            result.forbidden = True
            break

        for item in factory.items:
            store.set_emoji_id(
                guild.id, factory.key, item.key, emojis_by_name[item.emoji_name].id
            )

    return result


def build_embed(
    factory: Factory,
    emojis_by_name: dict[str, discord.Emoji],
    factories_dir: Path,
) -> tuple[discord.Embed, discord.File | None]:
    """Build a factory's embed and, when it has a picture on disk, its file attachment.

    The image is attached locally and referenced via ``attachment://`` so no external
    host is needed. A factory with ``image=None``, or whose image file is missing, is
    posted as an embed with no picture rather than raising.
    """
    lines = [DESCRIPTION_HEADER, ""]
    for item in factory.items:
        lines.append(f"{emojis_by_name[item.emoji_name]} — {item.role_name}")

    embed = discord.Embed(title=factory.name, description="\n".join(lines))

    if factory.image is None:
        return embed, None

    path = factories_dir / factory.image
    if not path.is_file():
        log.warning(
            "Factory %r image %s not found; posting without a picture.",
            factory.key,
            path,
        )
        return embed, None

    file = discord.File(path, filename=factory.image)
    embed.set_image(url=f"attachment://{factory.image}")
    return embed, file


async def seed_reactions(
    message: discord.Message,
    factory: Factory,
    emojis_by_name: dict[str, discord.Emoji],
) -> int:
    """Add each item's emoji to ``message`` as a reaction; return the count added.

    Shared by /setup-factories (after posting) and /reset-week (after clearing
    reactions). Re-adding a reaction the bot already placed is a no-op on Discord,
    so this is safe to re-run.
    """
    added = 0
    for item in factory.items:
        await message.add_reaction(emojis_by_name[item.emoji_name])
        added += 1
    return added


async def reset_reactions(
    guild: discord.Guild,
    channel: discord.abc.Messageable,
    config: FactoryConfig,
    store: StateStore,
    emojis_by_name: dict[str, discord.Emoji],
) -> ReactionResetResult:
    """Clear all reactions on every factory message and re-seed the bot's own.

    Used by /reset-week: ``message.clear_reactions()`` wipes every user's signup in
    one call per message, then ``seed_reactions`` restores the board to its initial
    state. Resolves emoji references first (they may have been deleted since setup)
    and aborts before touching anything if any are missing. A tracked message that
    has since been deleted is counted in ``already_gone`` and skipped; the first
    permission denial stops the run and reports ``forbidden``.
    """
    result = ReactionResetResult()

    result.missing_emojis = _find_missing_emojis(config, emojis_by_name)
    if result.missing_emojis:
        return result

    for factory in config.factories:
        message_id = store.get_message_id(guild.id, factory.key)
        if message_id is None:
            continue
        try:
            message = await channel.fetch_message(message_id)
        except discord.NotFound:
            result.already_gone += 1
            continue
        try:
            await message.clear_reactions()
            result.reactions_added += await seed_reactions(
                message, factory, emojis_by_name
            )
        except discord.Forbidden:
            log.error(
                "Missing permission to manage reactions in channel %s; "
                "grant Manage Messages and Add Reactions.",
                getattr(channel, "id", "?"),
            )
            result.forbidden = True
            return result
        result.messages_reset += 1

    return result


async def teardown_factories(
    guild: discord.Guild,
    channel: discord.abc.Messageable,
    config: FactoryConfig,
    store: StateStore,
) -> TeardownResult:
    """Delete every tracked factory message in ``channel`` and forget the board.

    Deleting a message removes its reactions, so signups shown as reactions are lost;
    the underlying roles are left untouched. On success the guild's channel and message
    IDs are cleared so a later /setup-factories can post fresh (in any channel). If a
    delete is denied, stops and leaves state intact so the admin can retry after fixing
    the Manage Messages permission.
    """
    result = TeardownResult()

    for factory in config.factories:
        message_id = store.get_message_id(guild.id, factory.key)
        if message_id is None:
            continue
        try:
            message = await channel.fetch_message(message_id)
        except discord.NotFound:
            result.already_gone += 1
            continue
        try:
            await message.delete()
        except discord.Forbidden:
            log.error(
                "Missing 'Manage Messages' in channel %s; cannot delete factory "
                "messages.",
                getattr(channel, "id", "?"),
            )
            result.forbidden = True
            return result

        result.deleted += 1

    store.reset_setup(guild.id)
    return result


def _find_missing_emojis(
    config: FactoryConfig, emojis_by_name: dict[str, discord.Emoji]
) -> list[str]:
    """Emoji names referenced by config but absent from the application, in order."""
    missing: list[str] = []
    for factory in config.factories:
        for item in factory.items:
            name = item.emoji_name
            if name not in emojis_by_name and name not in missing:
                missing.append(name)
    return missing


async def _upsert_message(
    channel: discord.abc.Messageable,
    factory: Factory,
    embed: discord.Embed,
    file: discord.File | None,
    store: StateStore,
    guild_id: int,
    result: FactorySetupResult,
) -> discord.Message:
    """Edit the factory's existing message if it's still there, else post a new one."""
    existing_id = store.get_message_id(guild_id, factory.key)
    if existing_id is not None:
        try:
            message = await channel.fetch_message(existing_id)
        except discord.NotFound:
            pass  # recorded message was deleted; fall through and repost
        else:
            await message.edit(embed=embed)
            result.refreshed.append(factory.name)
            return message

    if file is not None:
        message = await channel.send(embed=embed, file=file)
    else:
        message = await channel.send(embed=embed)
    store.set_message_id(guild_id, factory.key, message.id)
    result.posted.append(factory.name)
    return message
