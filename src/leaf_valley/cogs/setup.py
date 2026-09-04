"""Admin slash commands for idempotent setup. Currently: /create-roles."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

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


async def setup(bot: LeafValleyBot) -> None:
    await bot.add_cog(Setup(bot))
