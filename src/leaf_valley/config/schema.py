"""Typed, immutable representation of config/factories.yaml. Pure data, no I/O."""

from __future__ import annotations

from dataclasses import dataclass

# Discord caps a single message at 20 reactions.
MAX_ITEMS_PER_FACTORY = 20


@dataclass(frozen=True)
class Item:
    key: str
    role_name: str
    emoji: str  # ":cheese:" application-emoji reference

    @property
    def emoji_name(self) -> str:
        """The application emoji name to resolve, i.e. ':cheese:' -> 'cheese'."""
        return self.emoji.strip(":")


@dataclass(frozen=True)
class Factory:
    key: str
    name: str
    image: str | None  # file in assets/factories/, or None for an embed with no picture
    items: tuple[Item, ...]


@dataclass(frozen=True)
class FactoryConfig:
    factories: tuple[Factory, ...]
