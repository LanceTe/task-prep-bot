"""Admin slash commands for idempotent setup: /create-roles, /setup-factories, /teardown."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from leaf_valley import settings
from leaf_valley.services import factory_service
from leaf_valley.services.role_service import clear_all, create_missing_roles

if TYPE_CHECKING:
    from leaf_valley.bot import LeafValleyBot

log = logging.getLogger(__name__)


class _ConfirmView(discord.ui.View):
    """A two-button confirm/cancel prompt scoped to the admin who triggered it."""

    def __init__(
        self,
        author_id: int,
        *,
        confirm_label: str,
        progress_message: str,
        cancel_message: str,
    ) -> None:
        super().__init__(timeout=30)
        self.author_id = author_id
        self.confirmed = False
        self._progress_message = progress_message
        self._cancel_message = cancel_message
        self.confirm.label = confirm_label

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "This confirmation isn’t yours.", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(style=discord.ButtonStyle.danger)
    async def confirm(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        self.confirmed = True
        self._disable_all()
        self.stop()
        # Disable the buttons and show progress so a second click can't fire.
        await interaction.response.edit_message(
            content=self._progress_message, view=self
        )

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        self.confirmed = False
        self._disable_all()
        self.stop()
        await interaction.response.edit_message(content=self._cancel_message, view=None)

    def _disable_all(self) -> None:
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True


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
        description="Post/refresh each factory message in this channel and seed reactions.",
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

        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message(
                "Run this in the text channel where you want the factory messages "
                "posted.",
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

        if result.channel_conflict is not None:
            await interaction.followup.send(
                f"⚠️ Factories are already set up in <#{result.channel_conflict}>.\n\n"
                "I keep a single board per server, so I won’t post a second copy here. "
                f"To move the board to {channel.mention}, first run `/teardown` — that "
                "**deletes the existing factory messages and every reaction on them**, "
                "so only do it at the **end of a rally**. Then run `/setup-factories` "
                "here.",
                ephemeral=True,
            )
            return

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

    @app_commands.command(
        name="teardown",
        description="Delete all factory messages and their reactions. Use at the end of a rally.",
    )
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(manage_guild=True)
    async def teardown(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message(
                "This command must be run in a server.", ephemeral=True
            )
            return

        channel_id = self.bot.state.get_channel_id(guild.id)
        if channel_id is None:
            await interaction.response.send_message(
                "There are no factory messages set up to tear down.", ephemeral=True
            )
            return

        view = _ConfirmView(
            interaction.user.id,
            confirm_label="Delete messages",
            progress_message="Tearing down… deleting factory messages.",
            cancel_message="Teardown cancelled.",
        )
        await interaction.response.send_message(
            f"This will **delete every factory message in <#{channel_id}> and all "
            "reactions on them**. Item roles are kept, but signups shown as reactions "
            "will be lost, so only do this at the **end of a rally**.\n\nProceed?",
            view=view,
            ephemeral=True,
        )
        timed_out = await view.wait()
        if timed_out:
            await interaction.edit_original_response(
                content="Teardown timed out — nothing was deleted.", view=None
            )
            return
        if not view.confirmed:
            return  # the Cancel button already updated the message

        channel = guild.get_channel(channel_id)
        if not isinstance(channel, discord.TextChannel):
            # Channel was deleted or is inaccessible; just forget the board.
            self.bot.state.reset_setup(guild.id)
            self.bot.state.save()
            await interaction.edit_original_response(
                content=f"The tracked channel (<#{channel_id}>) is gone, so I cleared "
                "the saved setup. You can run `/setup-factories` fresh anywhere.",
                view=None,
            )
            return

        result = await factory_service.teardown_factories(
            guild, channel, self.bot.factory_config, self.bot.state
        )
        if not result.forbidden:
            self.bot.state.save()

        lines = [
            f"**Teardown — {guild.name}**",
            f"Deleted: {result.deleted}",
            f"Already gone: {result.already_gone}",
        ]
        if result.forbidden:
            lines.append(
                "\n⚠️ I’m missing the **Manage Messages** permission, so I couldn’t "
                "delete everything. Grant it, then run `/teardown` again."
            )
        await interaction.edit_original_response(content="\n".join(lines), view=None)

    @teardown.error
    async def teardown_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        if isinstance(error, app_commands.MissingPermissions):
            message = "You need the **Manage Server** permission to use this."
        else:
            log.exception("/teardown failed", exc_info=error)
            message = "Something went wrong running that command."
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)

    @app_commands.command(
        name="reset-week",
        description="Clear every item role from members and wipe reaction signups.",
    )
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(manage_guild=True)
    async def reset_week(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message(
                "This command must be run in a server.", ephemeral=True
            )
            return

        channel_id = self.bot.state.get_channel_id(guild.id)
        if channel_id is None:
            await interaction.response.send_message(
                "There's nothing to reset — run `/setup-factories` first.",
                ephemeral=True,
            )
            return

        view = _ConfirmView(
            interaction.user.id,
            confirm_label="Reset week",
            progress_message="Resetting… clearing roles and reactions.",
            cancel_message="Reset cancelled.",
        )
        await interaction.response.send_message(
            f"This will **remove every item role from all members** and **wipe all "
            f"reaction signups in <#{channel_id}>**, then re-seed the board's "
            "reactions for a fresh week. The factory messages themselves are kept."
            "\n\nProceed?",
            view=view,
            ephemeral=True,
        )
        timed_out = await view.wait()
        if timed_out:
            await interaction.edit_original_response(
                content="Reset timed out — nothing was changed.", view=None
            )
            return
        if not view.confirmed:
            return  # the Cancel button already updated the message

        emojis = await self.bot.fetch_application_emojis()
        emojis_by_name = {emoji.name: emoji for emoji in emojis}

        channel = guild.get_channel(channel_id)
        reaction_result = None
        if isinstance(channel, discord.TextChannel):
            reaction_result = await factory_service.reset_reactions(
                guild,
                channel,
                self.bot.factory_config,
                self.bot.state,
                emojis_by_name,
            )

        role_result = await clear_all(guild, self.bot.state.managed_role_ids(guild.id))

        lines = [
            f"**Reset week — {guild.name}**",
            f"Roles cleared: {role_result.roles_cleared}",
            f"Members affected: {role_result.members_affected}",
        ]

        if reaction_result is None:
            lines.append(
                f"\n⚠️ The board channel (<#{channel_id}>) is gone, so no reactions "
                "were reset. Roles were still cleared."
            )
        elif reaction_result.aborted:
            missing = ", ".join(
                f"`:{name}:`" for name in reaction_result.missing_emojis
            )
            lines.append(
                "\n⚠️ Reactions weren’t reset: these emojis are no longer uploaded: "
                f"{missing}. Run `uv run python scripts/seed_emojis.py`, then re-run."
            )
        else:
            lines.append(f"Messages reset: {reaction_result.messages_reset}")
            lines.append(f"Reactions re-seeded: {reaction_result.reactions_added}")
            if reaction_result.already_gone:
                lines.append(f"Already gone: {reaction_result.already_gone}")
            if reaction_result.forbidden:
                lines.append(
                    "\n⚠️ I’m missing permission to manage reactions in "
                    f"<#{channel_id}>, so some messages were skipped. Grant "
                    "**Manage Messages** and **Add Reactions**, then run this again."
                )

        if role_result.forbidden:
            lines.append(
                "\n⚠️ I’m missing the **Manage Roles** permission (or my role sits "
                "below the item roles), so some roles couldn’t be cleared. Fix that, "
                "then run this again."
            )

        await interaction.edit_original_response(content="\n".join(lines), view=None)

    @reset_week.error
    async def reset_week_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        if isinstance(error, app_commands.MissingPermissions):
            message = "You need the **Manage Server** permission to use this."
        else:
            log.exception("/reset-week failed", exc_info=error)
            message = "Something went wrong running that command."
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)


async def setup(bot: LeafValleyBot) -> None:
    await bot.add_cog(Setup(bot))
