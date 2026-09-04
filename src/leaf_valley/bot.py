"""LeafValleyBot: intents, shared config/state, cog loading, command sync."""

from __future__ import annotations

import logging

import discord
from discord.ext import commands

from leaf_valley import settings
from leaf_valley.config.loader import load_colour_config, load_factory_config
from leaf_valley.config.schema import ColourConfig, FactoryConfig
from leaf_valley.storage.state_store import StateStore

log = logging.getLogger(__name__)

# Cogs loaded on startup. Extend as later milestones add commands/listeners.
INITIAL_COGS = (
    "leaf_valley.cogs.setup",
    "leaf_valley.cogs.reaction_roles",
    "leaf_valley.cogs.colour_roles",
)


class LeafValleyBot(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.members = True  # required to add/remove roles and enumerate members
        super().__init__(command_prefix=commands.when_mentioned, intents=intents)

        # One shared config + state instance so the bot is the single state writer.
        self.factory_config: FactoryConfig = load_factory_config(
            settings.CONFIG_DIR / "factories.yaml"
        )
        self.colour_config: ColourConfig = load_colour_config(
            settings.CONFIG_DIR / "colours.yaml"
        )
        self.state: StateStore = StateStore.load(settings.DATA_DIR / "state.json")

    async def setup_hook(self) -> None:
        for cog in INITIAL_COGS:
            await self.load_extension(cog)
        if settings.GUILD_ID is not None:
            # Copy global commands to the dev guild and sync there for instant updates.
            guild = discord.Object(id=settings.GUILD_ID)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        else:
            await self.tree.sync()

    async def on_ready(self) -> None:
        log.info("Logged in as %s (id=%s)", self.user, getattr(self.user, "id", "?"))


def run() -> None:
    LeafValleyBot().run(settings.BOT_TOKEN)
