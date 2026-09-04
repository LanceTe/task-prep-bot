"""LeafValleyBot: intents, shared config/state, cog loading, command sync."""

from __future__ import annotations

import logging

import discord
from discord.ext import commands

from leaf_valley import settings
from leaf_valley.config.loader import load_factory_config
from leaf_valley.config.schema import FactoryConfig
from leaf_valley.storage.state_store import StateStore

log = logging.getLogger(__name__)

# Cogs loaded on startup. Extend as later milestones add commands/listeners.
INITIAL_COGS = ("leaf_valley.cogs.setup",)


class LeafValleyBot(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.members = True  # required to add/remove roles and enumerate members
        super().__init__(command_prefix=commands.when_mentioned, intents=intents)

        # One shared config + state instance so the bot is the single state writer.
        self.factory_config: FactoryConfig = load_factory_config(
            settings.CONFIG_DIR / "factories.yaml"
        )
        self.state: StateStore = StateStore.load(settings.DATA_DIR / "state.json")

    async def setup_hook(self) -> None:
        for cog in INITIAL_COGS:
            await self.load_extension(cog)
        await self.tree.sync()

    async def on_ready(self) -> None:
        log.info("Logged in as %s (id=%s)", self.user, getattr(self.user, "id", "?"))


def run() -> None:
    LeafValleyBot().run(settings.BOT_TOKEN)
