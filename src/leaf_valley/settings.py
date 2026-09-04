"""Central config: env vars + project paths. Import from here, don't call os.environ directly."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# src/leaf_valley/settings.py -> project root is 3 levels up.
PROJECT_ROOT: Path = Path(__file__).resolve().parents[2]

ASSETS_DIR: Path = PROJECT_ROOT / "assets"
EMOJIS_DIR: Path = ASSETS_DIR / "emojis"
FACTORIES_DIR: Path = ASSETS_DIR / "factories"
DATA_DIR: Path = PROJECT_ROOT / "data"
CONFIG_DIR: Path = PROJECT_ROOT / "config"

load_dotenv(PROJECT_ROOT / ".env")


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(
            f"Missing required environment variable: {name}. "
            f"Copy .env.example to .env and fill it in."
        )
    return value


BOT_TOKEN: str = _require_env("BOT_TOKEN")

# Optional dev guild: when set, commands sync to this guild instantly instead of
# globally (global syncs can take up to an hour to propagate).
_guild_id = os.environ.get("GUILD_ID")
GUILD_ID: int | None = int(_guild_id) if _guild_id else None
