"""Raw reaction listeners that grant/remove item roles as users react.

Reacting with an item's emoji on its factory message grants the matching role;
removing the reaction removes it (see PLAN §7). Raw events are used so the mappings
survive a bot restart — cached ``on_reaction_*`` events only fire for messages the
bot saw this session, whereas the factory board is long-lived.

The cog is thin: it maps (message, emoji) back to a role via the state store, then
delegates to role_service. Anything that isn't a managed reaction — the bot's own
seed reactions, unicode emojis, reactions on other messages, DMs — is ignored.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
from discord.ext import commands

from leaf_valley.services import role_service

if TYPE_CHECKING:
    from leaf_valley.bot import LeafValleyBot

log = logging.getLogger(__name__)


class ReactionRoles(commands.Cog):
    def __init__(self, bot: LeafValleyBot) -> None:
        self.bot = bot

    @commands.Cog.listener()
    async def on_raw_reaction_add(
        self, payload: discord.RawReactionActionEvent
    ) -> None:
        if not self._is_managed_reaction(payload):
            return
        role_id = self.bot.state.role_id_for_reaction(
            payload.guild_id, payload.message_id, payload.emoji.id
        )
        if role_id is None:
            return
        member = payload.member or self._member(payload.guild_id, payload.user_id)
        if member is None:
            return
        await role_service.assign_role(member, role_id)

    @commands.Cog.listener()
    async def on_raw_reaction_remove(
        self, payload: discord.RawReactionActionEvent
    ) -> None:
        if not self._is_managed_reaction(payload):
            return
        role_id = self.bot.state.role_id_for_reaction(
            payload.guild_id, payload.message_id, payload.emoji.id
        )
        if role_id is None:
            return
        # Remove events never carry payload.member, so always resolve it ourselves.
        member = self._member(payload.guild_id, payload.user_id)
        if member is None:
            return
        await role_service.remove_role(member, role_id)

    def _is_managed_reaction(self, payload: discord.RawReactionActionEvent) -> bool:
        """True unless this is a DM reaction or the bot's own seed reaction."""
        if payload.guild_id is None:
            return False
        return self.bot.user is None or payload.user_id != self.bot.user.id

    def _member(self, guild_id: int | None, user_id: int) -> discord.Member | None:
        if guild_id is None:
            return None
        guild = self.bot.get_guild(guild_id)
        return guild.get_member(user_id) if guild is not None else None


async def setup(bot: LeafValleyBot) -> None:
    await bot.add_cog(ReactionRoles(bot))
