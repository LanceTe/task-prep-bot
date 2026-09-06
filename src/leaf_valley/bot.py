"""LeafValleyBot: intents, shared config/state, cog loading, command sync."""

from __future__ import annotations

import logging

import discord
from discord.ext import commands

from leaf_valley import settings
from leaf_valley.config.loader import load_colour_config, load_factory_config
from leaf_valley.config.schema import ColourConfig, FactoryConfig
from leaf_valley.logging_config import configure_logging
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

        item_count = sum(len(f.items) for f in self.factory_config.factories)
        log.info(
            "Loaded config: %d factories (%d items), %d colours.",
            len(self.factory_config.factories),
            item_count,
            len(self.colour_config.colours),
        )

    async def setup_hook(self) -> None:
        for cog in INITIAL_COGS:
            await self.load_extension(cog)
        log.info("Loaded %d cog(s): %s.", len(INITIAL_COGS), ", ".join(INITIAL_COGS))
        if settings.GUILD_ID is not None:
            # Copy global commands to the dev guild and sync there for instant updates.
            guild = discord.Object(id=settings.GUILD_ID)
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
            log.info(
                "Synced %d application command(s) to guild %s.",
                len(synced),
                settings.GUILD_ID,
            )
        else:
            synced = await self.tree.sync()
            log.info("Synced %d application command(s) globally.", len(synced))

    async def on_ready(self) -> None:
        log.info("Logged in as %s (id=%s)", self.user, getattr(self.user, "id", "?"))


def run() -> None:
    configure_logging(settings.LOG_DIR, settings.LOG_LEVEL)
    # log_handler=None stops discord.py installing its own root StreamHandler on top.
    LeafValleyBot().run(settings.BOT_TOKEN, log_handler=None)
