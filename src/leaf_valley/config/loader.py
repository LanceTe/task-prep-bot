"""Parse and validate config/factories.yaml into typed schema objects.

Fails loudly (ConfigError) on any malformed input before the bot talks to Discord.
Has no dependency on settings.py, so it is testable offline: pass an explicit path.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from leaf_valley.config.schema import (
    MAX_ITEMS_PER_FACTORY,
    ColourConfig,
    ColourRole,
    Factory,
    FactoryConfig,
    Item,
)


class ConfigError(ValueError):
    """Raised when factories.yaml is missing, malformed, or fails validation."""


def load_factory_config(path: Path) -> FactoryConfig:
    if not path.is_file():
        raise ConfigError(f"Config file not found: {path}")

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"Invalid YAML in {path}: {exc}") from exc

    if not isinstance(raw, dict) or "factories" not in raw:
        raise ConfigError("Top-level 'factories' key is required.")

    raw_factories = raw["factories"]
    if not isinstance(raw_factories, list) or not raw_factories:
        raise ConfigError("'factories' must be a non-empty list.")

    factories: list[Factory] = []
    seen_keys: set[str] = set()
    for index, raw_factory in enumerate(raw_factories):
        factory = _parse_factory(raw_factory, index)
        if factory.key in seen_keys:
            raise ConfigError(f"Duplicate factory key: {factory.key!r}")
        seen_keys.add(factory.key)
        factories.append(factory)

    return FactoryConfig(factories=tuple(factories))


def load_colour_config(path: Path) -> ColourConfig:
    if not path.is_file():
        raise ConfigError(f"Config file not found: {path}")

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"Invalid YAML in {path}: {exc}") from exc

    if not isinstance(raw, dict) or "colours" not in raw:
        raise ConfigError("Top-level 'colours' key is required.")

    raw_colours = raw["colours"]
    if not isinstance(raw_colours, list) or not raw_colours:
        raise ConfigError("'colours' must be a non-empty list.")
    if len(raw_colours) > MAX_ITEMS_PER_FACTORY:
        raise ConfigError(
            f"'colours' has {len(raw_colours)} entries, exceeding the Discord "
            f"limit of {MAX_ITEMS_PER_FACTORY} reactions per message."
        )

    colours: list[ColourRole] = []
    seen_keys: set[str] = set()
    seen_emojis: set[str] = set()
    for index, raw_colour in enumerate(raw_colours):
        colour = _parse_colour(raw_colour, index)
        if colour.key in seen_keys:
            raise ConfigError(f"Duplicate colour key: {colour.key!r}")
        if colour.emoji in seen_emojis:
            raise ConfigError(
                f"Duplicate colour emoji {colour.emoji!r}; each colour needs a "
                "distinct reaction."
            )
        seen_keys.add(colour.key)
        seen_emojis.add(colour.emoji)
        colours.append(colour)

    return ColourConfig(colours=tuple(colours))


def _parse_factory(raw: Any, index: int) -> Factory:
    where = f"factory #{index + 1}"
    if not isinstance(raw, dict):
        raise ConfigError(f"{where} must be a mapping.")

    key = _require_str(raw, "key", where)
    where = f"factory {key!r}"
    name = _require_str(raw, "name", where)

    image = raw.get("image")
    if image is not None and not isinstance(image, str):
        raise ConfigError(f"{where}: 'image' must be a string or null.")

    raw_items = raw.get("items")
    if not isinstance(raw_items, list) or not raw_items:
        raise ConfigError(f"{where}: 'items' must be a non-empty list.")
    if len(raw_items) > MAX_ITEMS_PER_FACTORY:
        raise ConfigError(
            f"{where}: has {len(raw_items)} items, exceeding the Discord "
            f"limit of {MAX_ITEMS_PER_FACTORY} reactions per message."
        )

    items: list[Item] = []
    seen_item_keys: set[str] = set()
    for item_index, raw_item in enumerate(raw_items):
        item = _parse_item(raw_item, key, item_index)
        if item.key in seen_item_keys:
            raise ConfigError(f"{where}: duplicate item key {item.key!r}")
        seen_item_keys.add(item.key)
        items.append(item)

    return Factory(
        key=key,
        name=name,
        image=image,
        items=tuple(items),
    )


def _parse_item(raw: Any, factory_key: str, index: int) -> Item:
    where = f"factory {factory_key!r} item #{index + 1}"
    if not isinstance(raw, dict):
        raise ConfigError(f"{where} must be a mapping.")

    key = _require_str(raw, "key", where)
    role_name = _require_str(raw, "role_name", where)
    emoji = _require_str(raw, "emoji", where)

    if not (emoji.startswith(":") and emoji.endswith(":") and len(emoji) > 2):
        raise ConfigError(
            f"{where}: 'emoji' must be an application-emoji reference like ':cheese:', got {emoji!r}."
        )

    return Item(key=key, role_name=role_name, emoji=emoji)


def _parse_colour(raw: Any, index: int) -> ColourRole:
    where = f"colour #{index + 1}"
    if not isinstance(raw, dict):
        raise ConfigError(f"{where} must be a mapping.")

    key = _require_str(raw, "key", where)
    where = f"colour {key!r}"
    role_name = _require_str(raw, "role_name", where)
    emoji = _require_str(raw, "emoji", where)

    if emoji.startswith(":") and emoji.endswith(":"):
        raise ConfigError(
            f"{where}: 'emoji' must be a unicode emoji like '🔴', not an "
            f"application-emoji reference, got {emoji!r}."
        )

    raw_hex = _require_str(raw, "colour", where)
    colour = _parse_hex_colour(raw_hex, where)

    return ColourRole(key=key, role_name=role_name, emoji=emoji, colour=colour)


def _parse_hex_colour(raw: str, where: str) -> int:
    value = raw.lstrip("#")
    if len(value) != 6:
        raise ConfigError(
            f"{where}: 'colour' must be a '#RRGGBB' hex string, got {raw!r}."
        )
    try:
        return int(value, 16)
    except ValueError as exc:
        raise ConfigError(
            f"{where}: 'colour' must be a '#RRGGBB' hex string, got {raw!r}."
        ) from exc


def _require_str(raw: dict[str, Any], field: str, where: str) -> str:
    value = raw.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(
            f"{where}: '{field}' is required and must be a non-empty string."
        )
    return value
