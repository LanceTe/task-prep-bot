"""Raw reaction listeners for the name-colour board: pick one colour at a time.

Reacting a colour on the board grants that colour role and removes any colour the
member already had (roles and their reactions), so a member always shows exactly one
name colour. Removing a colour reaction removes that colour role.

Raw events are used so the mapping survives a restart. This cog only ever acts on the
colour board message with a configured colour emoji; the item ``reaction_roles`` cog
only matches custom application emojis, so the two never collide even though both see
every reaction event.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
from discord.ext import commands

from leaf_valley.services import colour_service, role_service

if TYPE_CHECKING:
    from leaf_valley.bot import LeafValleyBot

log = logging.getLogger(__name__)


class ColourRoles(commands.Cog):
    def __init__(self, bot: LeafValleyBot) -> None:
        self.bot = bot

    @commands.Cog.listener()
    async def on_raw_reaction_add(
        self, payload: discord.RawReactionActionEvent
    ) -> None:
        colour = self._colour_for_payload(payload)
        if colour is None:
            return
        chosen_role_id, member = colour
        message = await self._fetch_board(payload)
        if message is None:
            return
        others = self._other_held_colours(payload.guild_id, member, chosen_role_id)
        await colour_service.apply_exclusive_colour(
            member, message, chosen_role_id, others
        )

    @commands.Cog.listener()
    async def on_raw_reaction_remove(
        self, payload: discord.RawReactionActionEvent
    ) -> None:
        colour = self._colour_for_payload(payload)
        if colour is None:
            return
        role_id, member = colour
        await role_service.remove_role(member, role_id)

    def _colour_for_payload(
        self, payload: discord.RawReactionActionEvent
    ) -> tuple[int, discord.Member] | None:
        """Resolve a colour reaction to its (role_id, member), or None if it isn't one.

        Ignores DMs, the bot's own seed reactions, reactions on other messages, and
        any emoji that isn't a configured colour.
        """
        if payload.guild_id is None:
            return None
        if self.bot.user is not None and payload.user_id == self.bot.user.id:
            return None
        if payload.message_id != self.bot.state.get_colour_message_id(payload.guild_id):
            return None
        if payload.emoji.id is not None:  # colour reactions are always unicode
            return None

        colour_key = self._colour_key_for_emoji(payload.emoji.name)
        if colour_key is None:
            return None
        role_id = self.bot.state.get_colour_role_id(payload.guild_id, colour_key)
        if role_id is None:
            return None

        member = payload.member or self._member(payload.guild_id, payload.user_id)
        if member is None:
            return None
        return role_id, member

    def _colour_key_for_emoji(self, emoji: str) -> str | None:
        for colour in self.bot.colour_config.colours:
            if colour.emoji == emoji:
                return colour.key
        return None

    def _other_held_colours(
        self, guild_id: int, member: discord.Member, chosen_role_id: int
    ) -> dict[int, str]:
        """Map each other colour role the member holds to its board emoji."""
        held = {role.id for role in member.roles}
        others: dict[int, str] = {}
        for colour in self.bot.colour_config.colours:
            role_id = self.bot.state.get_colour_role_id(guild_id, colour.key)
            if role_id is not None and role_id != chosen_role_id and role_id in held:
                others[role_id] = colour.emoji
        return others

    async def _fetch_board(
        self, payload: discord.RawReactionActionEvent
    ) -> discord.Message | None:
        channel = self.bot.get_channel(payload.channel_id)
        if not isinstance(channel, discord.abc.Messageable):
            return None
        try:
            return await channel.fetch_message(payload.message_id)
        except (discord.NotFound, discord.Forbidden):
            return None

    def _member(self, guild_id: int, user_id: int) -> discord.Member | None:
        guild = self.bot.get_guild(guild_id)
        return guild.get_member(user_id) if guild is not None else None


async def setup(bot: LeafValleyBot) -> None:
    await bot.add_cog(ColourRoles(bot))
