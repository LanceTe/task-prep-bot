"""Admin slash commands for idempotent setup. Currently: /create-roles."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from leaf_valley import settings
from leaf_valley.services import factory_service
from leaf_valley.services.role_service import create_missing_roles

if TYPE_CHECKING:
    from leaf_valley.bot import LeafValleyBot

log = logging.getLogger(__name__)


class Setup(commands.Cog):
    def __init__(self, bot: LeafValleyBot) -> None:
        self.bot = bot

    @app_commands.command(
        name="create-roles",
        description="Create any missing item roles and link them in state (idempotent).",
    )
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(manage_guild=True)
    async def create_roles(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message(
                "This command must be run in a server.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        result = await create_missing_roles(
            guild, self.bot.factory_config, self.bot.state
        )
        if result.changed:
            self.bot.state.save()

        lines = [
            f"**Create roles — {guild.name}**",
            f"Created: {len(result.created)}",
            f"Adopted: {len(result.adopted)}",
            f"Already linked: {len(result.existing)}",
        ]
        if result.forbidden:
            lines.append(
                "\n⚠️ I’m missing the **Manage Roles** permission, so some roles "
                "couldn’t be created. Grant it, move my role above the item roles, "
                "then run this again."
            )
        await interaction.followup.send("\n".join(lines), ephemeral=True)

    @create_roles.error
    async def create_roles_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        if isinstance(error, app_commands.MissingPermissions):
            message = "You need the **Manage Server** permission to use this."
        else:
            log.exception("/create-roles failed", exc_info=error)
            message = "Something went wrong running that command."
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)

    @app_commands.command(
        name="setup-factories",
        description="Post/refresh each factory message and seed its reactions (idempotent).",
    )
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(manage_guild=True)
    async def setup_factories(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message(
                "This command must be run in a server.", ephemeral=True
            )
            return

        channel = guild.get_channel(settings.FACTORY_CHANNEL_ID)
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message(
                f"The configured `FACTORY_CHANNEL_ID` ({settings.FACTORY_CHANNEL_ID}) "
                "isn’t a text channel in this server. Fix it in `.env` and try again.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        emojis = await self.bot.fetch_application_emojis()
        emojis_by_name = {emoji.name: emoji for emoji in emojis}

        result = await factory_service.setup_factories(
            guild,
            channel,
            self.bot.factory_config,
            self.bot.state,
            emojis_by_name,
            settings.FACTORIES_DIR,
        )
        if result.changed:
            self.bot.state.save()

        if result.aborted:
            missing = ", ".join(f"`:{name}:`" for name in result.missing_emojis)
            await interaction.followup.send(
                f"⚠️ These emojis referenced in `factories.yaml` aren’t uploaded yet: "
                f"{missing}\nRun `uv run python scripts/seed_emojis.py`, then try again.",
                ephemeral=True,
            )
            return

        lines = [
            f"**Setup factories — {guild.name}**",
            f"Posted: {len(result.posted)}",
            f"Refreshed: {len(result.refreshed)}",
            f"Reactions seeded: {result.reactions_added}",
        ]
        if result.forbidden:
            lines.append(
                "\n⚠️ I’m missing permission to post or react in "
                f"{channel.mention}, so some factories were skipped. Grant me "
                "**View Channel**, **Send Messages** and **Add Reactions**, then run "
                "this again."
            )
        await interaction.followup.send("\n".join(lines), ephemeral=True)

    @setup_factories.error
    async def setup_factories_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        if isinstance(error, app_commands.MissingPermissions):
            message = "You need the **Manage Server** permission to use this."
        else:
            log.exception("/setup-factories failed", exc_info=error)
            message = "Something went wrong running that command."
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)


async def setup(bot: LeafValleyBot) -> None:
    await bot.add_cog(Setup(bot))
