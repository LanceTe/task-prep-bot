from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import discord

from leaf_valley.config.schema import Factory, FactoryConfig, Item
from leaf_valley.services.factory_service import setup_factories
from leaf_valley.storage.state_store import StateStore


class FakeEmoji:
    def __init__(self, id: int, name: str) -> None:
        self.id = id
        self.name = name

    def __str__(self) -> str:
        return f"<:{self.name}:{self.id}>"


class FakeMessage:
    def __init__(self, id: int) -> None:
        self.id = id
        self.reactions: list[FakeEmoji] = []
        self.embed: discord.Embed | None = None
        self.edited = False

    async def add_reaction(self, emoji: FakeEmoji) -> None:
        self.reactions.append(emoji)

    async def edit(self, *, embed: discord.Embed) -> None:
        self.embed = embed
        self.edited = True


class FakeChannel:
    """Minimal duck-typed stand-in for a discord.TextChannel."""

    def __init__(
        self,
        id: int = 1,
        existing: dict[int, FakeMessage] | None = None,
        *,
        forbid_send: bool = False,
    ) -> None:
        self.id = id
        self.forbid_send = forbid_send
        self.sent: list[FakeMessage] = []
        self.messages: dict[int, FakeMessage] = existing or {}
        self._next_id = id * 1000

    async def send(
        self, *, embed: discord.Embed, file: discord.File | None = None
    ) -> FakeMessage:
        if self.forbid_send:
            resp = SimpleNamespace(status=403, reason="Forbidden")
            raise discord.Forbidden(resp, "missing Send Messages")
        self._next_id += 1
        message = FakeMessage(self._next_id)
        message.embed = embed
        self.sent.append(message)
        self.messages[message.id] = message
        return message

    async def fetch_message(self, id: int) -> FakeMessage:
        message = self.messages.get(id)
        if message is None:
            resp = SimpleNamespace(status=404, reason="Not Found")
            raise discord.NotFound(resp, "unknown message")
        return message


def _config() -> FactoryConfig:
    return FactoryConfig(
        factories=(
            Factory(
                key="dairy",
                name="🥛 Dairy",
                image=None,
                items=(
                    Item(key="cheese", role_name="cheese", emoji=":cheese:"),
                    Item(key="cream", role_name="cream", emoji=":cream:"),
                ),
            ),
        )
    )


def _emojis() -> dict[str, FakeEmoji]:
    return {"cheese": FakeEmoji(10, "cheese"), "cream": FakeEmoji(11, "cream")}


def _guild(id: int = 111) -> SimpleNamespace:
    return SimpleNamespace(id=id)


def _store(tmp_path: Path) -> StateStore:
    return StateStore.load(tmp_path / "state.json")


def _run(channel: FakeChannel, store: StateStore, tmp_path: Path, emojis=None):
    return asyncio.run(
        setup_factories(
            _guild(),
            channel,
            _config(),
            store,
            emojis if emojis is not None else _emojis(),
            tmp_path,
        )
    )


def test_posts_message_and_seeds_reactions(tmp_path: Path) -> None:
    channel = FakeChannel()
    store = _store(tmp_path)

    result = _run(channel, store, tmp_path)

    assert result.posted == ["🥛 Dairy"]
    assert result.refreshed == []
    assert result.reactions_added == 2
    assert result.aborted is False
    assert result.forbidden is False
    assert result.changed is True

    assert len(channel.sent) == 1
    message = channel.sent[0]
    assert [emoji.name for emoji in message.reactions] == ["cheese", "cream"]
    # Message and emoji IDs are recorded so reaction events can map back later.
    assert store.get_message_id(111, "dairy") == message.id
    guild_state = store.get_guild(111)
    assert guild_state is not None
    items = guild_state.factories["dairy"].items
    assert items["cheese"].emoji_id == 10
    assert items["cream"].emoji_id == 11


def test_missing_emoji_aborts_before_posting(tmp_path: Path) -> None:
    channel = FakeChannel()
    store = _store(tmp_path)

    result = _run(channel, store, tmp_path, emojis={"cheese": FakeEmoji(10, "cheese")})

    assert result.missing_emojis == ["cream"]
    assert result.aborted is True
    assert result.changed is False
    # Nothing was posted and no state was written.
    assert channel.sent == []
    assert store.get_message_id(111, "dairy") is None


def test_existing_message_is_refreshed_not_reposted(tmp_path: Path) -> None:
    existing = FakeMessage(555)
    channel = FakeChannel(existing={555: existing})
    store = _store(tmp_path)
    store.set_message_id(111, "dairy", 555)

    result = _run(channel, store, tmp_path)

    assert result.refreshed == ["🥛 Dairy"]
    assert result.posted == []
    assert channel.sent == []
    assert existing.edited is True
    # Reactions are re-seeded on the existing message.
    assert [emoji.name for emoji in existing.reactions] == ["cheese", "cream"]
    assert store.get_message_id(111, "dairy") == 555


def test_stale_message_id_reposts(tmp_path: Path) -> None:
    # State points at a message that no longer exists in the channel.
    channel = FakeChannel()
    store = _store(tmp_path)
    store.set_message_id(111, "dairy", 999)

    result = _run(channel, store, tmp_path)

    assert result.posted == ["🥛 Dairy"]
    assert result.refreshed == []
    assert len(channel.sent) == 1
    assert store.get_message_id(111, "dairy") == channel.sent[0].id


def test_forbidden_flags_and_skips(tmp_path: Path) -> None:
    channel = FakeChannel(forbid_send=True)
    store = _store(tmp_path)

    result = _run(channel, store, tmp_path)

    assert result.forbidden is True
    assert result.posted == []
    assert result.reactions_added == 0
    assert result.changed is False
    assert store.get_message_id(111, "dairy") is None


def test_image_is_attached_when_file_exists(tmp_path: Path) -> None:
    factories_dir = tmp_path / "factories"
    factories_dir.mkdir()
    (factories_dir / "dairy.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    config = FactoryConfig(
        factories=(
            Factory(
                key="dairy",
                name="🥛 Dairy",
                image="dairy.png",
                items=(Item(key="cheese", role_name="cheese", emoji=":cheese:"),),
            ),
        )
    )
    channel = FakeChannel()
    store = _store(tmp_path)

    result = asyncio.run(
        setup_factories(
            _guild(),
            channel,
            config,
            store,
            {"cheese": FakeEmoji(10, "cheese")},
            factories_dir,
        )
    )

    assert result.posted == ["🥛 Dairy"]
    message = channel.sent[0]
    assert message.embed is not None
    assert message.embed.image.url == "attachment://dairy.png"
