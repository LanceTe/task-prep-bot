"""Read/write the bot-managed runtime state file (data/state.json).

The bot owns this file: it writes the Discord IDs it discovers at runtime
(created role IDs, posted message IDs, uploaded emoji IDs) so that mappings
survive a restart. Config (factories.yaml) is the source of truth for *what*
exists; this store records the *runtime IDs* that back those definitions.

State is keyed by guild so a single bot can serve multiple servers, each with
its own independent set of role/message/emoji IDs.

Shape on disk::

    {
      "guilds": {
        "111": {
          "channel_id": 999,
          "factories": {
            "milk_factory": {
              "message_id": 222,
              "items": {
                "cheese": { "role_id": 333, "emoji_id": null }
              }
            }
          },
          "colours": {
            "channel_id": 999,
            "message_id": 424,
            "roles": { "red": 555 }
          }
        }
      }
    }

Writes are atomic (temp file + os.replace) so a crash mid-save can't corrupt
an existing state file. Mutations happen in memory; call save() to persist.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class ItemState:
    """Runtime IDs for one item under a factory."""

    role_id: int | None = None
    emoji_id: int | None = None  # None for unicode emoji; set for custom app emoji


@dataclass
class FactoryState:
    """Runtime IDs for one factory: its posted message and its items."""

    message_id: int | None = None
    items: dict[str, ItemState] = field(default_factory=dict)


@dataclass
class ColourState:
    """Runtime IDs for the single name-colour picker board and its colour roles."""

    channel_id: int | None = None
    message_id: int | None = None
    # colour key -> role_id
    roles: dict[str, int] = field(default_factory=dict)


@dataclass
class GuildState:
    """All factory state for a single guild."""

    # The one channel this guild's factory board is posted in; None until setup runs.
    channel_id: int | None = None
    factories: dict[str, FactoryState] = field(default_factory=dict)
    colours: ColourState = field(default_factory=ColourState)


class StateStore:
    """In-memory view of state.json with atomic persistence.

    Load once, mutate via the set_* helpers (which create nested entries as
    needed), then call save(). Read helpers never create entries, so an event
    for an unknown message/role returns None rather than growing the file.
    """

    def __init__(self, path: Path, guilds: dict[int, GuildState] | None = None) -> None:
        self.path = path
        self.guilds: dict[int, GuildState] = guilds if guilds is not None else {}

    # --- persistence -----------------------------------------------------

    @classmethod
    def load(cls, path: Path) -> StateStore:
        """Load state from ``path``. A missing file yields empty state."""
        if not path.is_file():
            return cls(path)

        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            raise StateError(f"Could not read state file {path}: {exc}") from exc

        return cls(path, guilds=_parse_guilds(raw))

    def save(self) -> None:
        """Atomically write current state to ``path`` (creating parent dirs)."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(self._to_dict(), indent=2), encoding="utf-8")
        os.replace(tmp, self.path)

    # --- writes (get-or-create) -----------------------------------------

    def set_message_id(self, guild_id: int, factory_key: str, message_id: int) -> None:
        self._factory(guild_id, factory_key).message_id = message_id

    def set_channel_id(self, guild_id: int, channel_id: int) -> None:
        self.guilds.setdefault(guild_id, GuildState()).channel_id = channel_id

    def reset_setup(self, guild_id: int) -> None:
        """Forget the posted board: clear the channel and every message ID.

        Roles and emoji IDs are left intact, so re-running setup re-posts the board
        while existing signups (held as roles) survive. Used by /teardown.
        """
        guild = self.guilds.get(guild_id)
        if guild is None:
            return
        guild.channel_id = None
        for factory in guild.factories.values():
            factory.message_id = None

    def set_role_id(
        self, guild_id: int, factory_key: str, item_key: str, role_id: int
    ) -> None:
        self._item(guild_id, factory_key, item_key).role_id = role_id

    def set_emoji_id(
        self, guild_id: int, factory_key: str, item_key: str, emoji_id: int | None
    ) -> None:
        self._item(guild_id, factory_key, item_key).emoji_id = emoji_id

    def set_colour_channel_id(self, guild_id: int, channel_id: int) -> None:
        self.guilds.setdefault(guild_id, GuildState()).colours.channel_id = channel_id

    def set_colour_message_id(self, guild_id: int, message_id: int) -> None:
        self.guilds.setdefault(guild_id, GuildState()).colours.message_id = message_id

    def set_colour_role_id(self, guild_id: int, colour_key: str, role_id: int) -> None:
        self.guilds.setdefault(guild_id, GuildState()).colours.roles[colour_key] = (
            role_id
        )

    # --- reads (never create) -------------------------------------------

    def get_guild(self, guild_id: int) -> GuildState | None:
        return self.guilds.get(guild_id)

    def get_channel_id(self, guild_id: int) -> int | None:
        """The channel this guild's factory board is posted in, or None if unset."""
        guild = self.guilds.get(guild_id)
        return guild.channel_id if guild is not None else None

    def factory_key_for_message(self, guild_id: int, message_id: int) -> str | None:
        """Reverse lookup used by reaction events: which factory owns a message."""
        guild = self.guilds.get(guild_id)
        if guild is None:
            return None
        for factory_key, factory in guild.factories.items():
            if factory.message_id == message_id:
                return factory_key
        return None

    def get_message_id(self, guild_id: int, factory_key: str) -> int | None:
        """The posted message ID for a factory, or None if not yet posted."""
        guild = self.guilds.get(guild_id)
        if guild is None:
            return None
        factory = guild.factories.get(factory_key)
        return factory.message_id if factory is not None else None

    def get_role_id(self, guild_id: int, factory_key: str, item_key: str) -> int | None:
        guild = self.guilds.get(guild_id)
        if guild is None:
            return None
        factory = guild.factories.get(factory_key)
        if factory is None:
            return None
        item = factory.items.get(item_key)
        return item.role_id if item is not None else None

    def role_id_for_reaction(
        self, guild_id: int, message_id: int, emoji_id: int | None
    ) -> int | None:
        """Map a reaction (message + custom emoji) back to its item role ID.

        Used by the reaction listeners: finds the factory owning ``message_id`` and
        the item whose recorded ``emoji_id`` matches, returning its role ID. Managed
        reactions are always custom application emojis, so a ``None`` ``emoji_id``
        (a unicode reaction) never matches and returns None.
        """
        if emoji_id is None:
            return None
        guild = self.guilds.get(guild_id)
        if guild is None:
            return None
        for factory in guild.factories.values():
            if factory.message_id != message_id:
                continue
            for item in factory.items.values():
                if item.emoji_id == emoji_id:
                    return item.role_id
            return None
        return None

    def managed_role_ids(self, guild_id: int) -> set[int]:
        """Every known item role ID for a guild (for the weekly reset)."""
        guild = self.guilds.get(guild_id)
        if guild is None:
            return set()
        return {
            item.role_id
            for factory in guild.factories.values()
            for item in factory.items.values()
            if item.role_id is not None
        }

    def get_colour_channel_id(self, guild_id: int) -> int | None:
        """The channel the colour board is posted in, or None if unset."""
        guild = self.guilds.get(guild_id)
        return guild.colours.channel_id if guild is not None else None

    def get_colour_message_id(self, guild_id: int) -> int | None:
        """The posted colour board message ID, or None if not yet posted."""
        guild = self.guilds.get(guild_id)
        return guild.colours.message_id if guild is not None else None

    def get_colour_role_id(self, guild_id: int, colour_key: str) -> int | None:
        guild = self.guilds.get(guild_id)
        return guild.colours.roles.get(colour_key) if guild is not None else None

    def managed_colour_role_ids(self, guild_id: int) -> set[int]:
        """Every known colour role ID for a guild (for enforcing one colour)."""
        guild = self.guilds.get(guild_id)
        if guild is None:
            return set()
        return set(guild.colours.roles.values())

    # --- internal --------------------------------------------------------

    def _factory(self, guild_id: int, factory_key: str) -> FactoryState:
        guild = self.guilds.setdefault(guild_id, GuildState())
        return guild.factories.setdefault(factory_key, FactoryState())

    def _item(self, guild_id: int, factory_key: str, item_key: str) -> ItemState:
        factory = self._factory(guild_id, factory_key)
        return factory.items.setdefault(item_key, ItemState())

    def _to_dict(self) -> dict[str, Any]:
        return {
            "guilds": {
                str(guild_id): {
                    "channel_id": guild.channel_id,
                    "factories": {
                        factory_key: {
                            "message_id": factory.message_id,
                            "items": {
                                item_key: {
                                    "role_id": item.role_id,
                                    "emoji_id": item.emoji_id,
                                }
                                for item_key, item in factory.items.items()
                            },
                        }
                        for factory_key, factory in guild.factories.items()
                    },
                    "colours": {
                        "channel_id": guild.colours.channel_id,
                        "message_id": guild.colours.message_id,
                        "roles": dict(guild.colours.roles),
                    },
                }
                for guild_id, guild in self.guilds.items()
            }
        }


class StateError(RuntimeError):
    """Raised when the state file exists but cannot be read or parsed."""


def _parse_guilds(raw: Any) -> dict[int, GuildState]:
    if not isinstance(raw, dict):
        raise StateError("State file root must be a JSON object.")

    guilds: dict[int, GuildState] = {}
    for guild_id_str, raw_guild in (raw.get("guilds") or {}).items():
        try:
            guild_id = int(guild_id_str)
        except (TypeError, ValueError) as exc:
            raise StateError(f"Invalid guild id key: {guild_id_str!r}") from exc

        factories: dict[str, FactoryState] = {}
        for factory_key, raw_factory in (raw_guild.get("factories") or {}).items():
            items = {
                item_key: ItemState(
                    role_id=raw_item.get("role_id"),
                    emoji_id=raw_item.get("emoji_id"),
                )
                for item_key, raw_item in (raw_factory.get("items") or {}).items()
            }
            factories[factory_key] = FactoryState(
                message_id=raw_factory.get("message_id"),
                items=items,
            )
        raw_colours = raw_guild.get("colours") or {}
        colours = ColourState(
            channel_id=raw_colours.get("channel_id"),
            message_id=raw_colours.get("message_id"),
            roles={
                key: int(role_id)
                for key, role_id in (raw_colours.get("roles") or {}).items()
            },
        )
        guilds[guild_id] = GuildState(
            channel_id=raw_guild.get("channel_id"),
            factories=factories,
            colours=colours,
        )

    return guilds
