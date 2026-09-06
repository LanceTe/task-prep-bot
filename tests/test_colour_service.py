from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import discord

from leaf_valley.config.schema import ColourConfig, ColourRole
from leaf_valley.services.colour_service import (
    apply_exclusive_colour,
    create_missing_colour_roles,
    setup_colour_board,
)
from leaf_valley.storage.state_store import StateStore

GUILD_ID = 111


class FakeRole:
    def __init__(self, id: int, name: str) -> None:
        self.id = id
        self.name = name


class FakeMember:
    def __init__(
        self, id: int, guild: FakeGuild, *, roles: tuple[FakeRole, ...] = ()
    ) -> None:
        self.id = id
        self.guild = guild
        self.display_name = f"member-{id}"
        self.roles = list(roles)
        self.added: list[FakeRole] = []
        self.removed: list[FakeRole] = []

    async def add_roles(self, role: FakeRole, *, reason: str) -> None:
        self.added.append(role)
        if role not in self.roles:
            self.roles.append(role)

    async def remove_roles(self, role: FakeRole, *, reason: str) -> None:
        self.removed.append(role)
        if role in self.roles:
            self.roles.remove(role)


class FakeGuild:
    def __init__(self, id: int, roles: tuple[FakeRole, ...] = ()) -> None:
        self.id = id
        self.roles = list(roles)
        self.created: list[tuple[str, int, bool]] = []
        self._next_id = id * 1000

    def get_role(self, role_id: int) -> FakeRole | None:
        return next((r for r in self.roles if r.id == role_id), None)

    async def create_role(
        self, *, name: str, colour: discord.Colour, mentionable: bool, reason: str
    ) -> FakeRole:
        self._next_id += 1
        role = FakeRole(self._next_id, name)
        self.roles.append(role)
        self.created.append((name, colour.value, mentionable))
        return role


class FakeMessage:
    def __init__(self, id: int) -> None:
        self.id = id
        self.reactions: list[str] = []
        self.removed_reactions: list[tuple[str, int]] = []
        self.embed: discord.Embed | None = None
        self.edited = False

    async def add_reaction(self, emoji: str) -> None:
        self.reactions.append(emoji)

    async def edit(self, *, embed: discord.Embed) -> None:
        self.embed = embed
        self.edited = True

    async def remove_reaction(self, emoji: str, member: FakeMember) -> None:
        self.removed_reactions.append((emoji, member.id))


class FakeChannel:
    def __init__(self, id: int) -> None:
        self.id = id
        self.sent: list[FakeMessage] = []
        self._messages: dict[int, FakeMessage] = {}
        self._next_id = 5000

    async def send(self, *, embed: discord.Embed) -> FakeMessage:
        self._next_id += 1
        message = FakeMessage(self._next_id)
        message.embed = embed
        self.sent.append(message)
        self._messages[message.id] = message
        return message

    async def fetch_message(self, message_id: int) -> FakeMessage:
        message = self._messages.get(message_id)
        if message is None:
            resp = SimpleNamespace(status=404, reason="Not Found")
            raise discord.NotFound(resp, "unknown message")
        return message


def _config() -> ColourConfig:
    return ColourConfig(
        colours=(
            ColourRole(key="red", role_name="Red", emoji="🔴", colour=0xE74C3C),
            ColourRole(key="blue", role_name="Blue", emoji="🔵", colour=0x3498DB),
        )
    )


def _store(tmp_path: Path) -> StateStore:
    return StateStore.load(tmp_path / "state.json")


def test_creates_missing_colour_roles(tmp_path: Path) -> None:
    guild = FakeGuild(GUILD_ID)
    store = _store(tmp_path)

    result = asyncio.run(create_missing_colour_roles(guild, _config(), store))

    assert result.created == ["Red", "Blue"]
    assert result.changed is True
    # Colour roles are non-mentionable and carry the configured colour value.
    assert all(not mentionable for _, _, mentionable in guild.created)
    assert guild.created[0][1] == 0xE74C3C
    assert store.get_colour_role_id(GUILD_ID, "red") is not None
    assert store.get_colour_role_id(GUILD_ID, "blue") is not None


def test_adopts_existing_role_by_name(tmp_path: Path) -> None:
    guild = FakeGuild(GUILD_ID, roles=(FakeRole(555, "Red"),))
    store = _store(tmp_path)

    result = asyncio.run(create_missing_colour_roles(guild, _config(), store))

    assert result.adopted == ["Red"]
    assert result.created == ["Blue"]
    assert store.get_colour_role_id(GUILD_ID, "red") == 555


def test_existing_state_left_untouched(tmp_path: Path) -> None:
    guild = FakeGuild(GUILD_ID, roles=(FakeRole(555, "Red"), FakeRole(556, "Blue")))
    store = _store(tmp_path)
    store.set_colour_role_id(GUILD_ID, "red", 555)
    store.set_colour_role_id(GUILD_ID, "blue", 556)

    result = asyncio.run(create_missing_colour_roles(guild, _config(), store))

    assert result.existing == ["Red", "Blue"]
    assert result.created == []
    assert result.changed is False
    assert guild.created == []


def test_setup_posts_board_and_seeds_reactions(tmp_path: Path) -> None:
    guild = FakeGuild(GUILD_ID)
    channel = FakeChannel(999)
    store = _store(tmp_path)

    result = asyncio.run(setup_colour_board(guild, channel, _config(), store))

    assert result.posted is True
    assert result.reactions_added == 2
    assert channel.sent[0].reactions == ["🔴", "🔵"]
    assert store.get_colour_channel_id(GUILD_ID) == 999
    assert store.get_colour_message_id(GUILD_ID) == channel.sent[0].id


def test_setup_refreshes_existing_board(tmp_path: Path) -> None:
    guild = FakeGuild(GUILD_ID)
    channel = FakeChannel(999)
    store = _store(tmp_path)
    asyncio.run(setup_colour_board(guild, channel, _config(), store))

    result = asyncio.run(setup_colour_board(guild, channel, _config(), store))

    assert result.refreshed is True
    assert result.posted is False
    # No second message was posted.
    assert len(channel.sent) == 1


def test_setup_rejects_second_channel(tmp_path: Path) -> None:
    guild = FakeGuild(GUILD_ID)
    store = _store(tmp_path)
    asyncio.run(setup_colour_board(guild, FakeChannel(999), _config(), store))

    result = asyncio.run(setup_colour_board(guild, FakeChannel(1000), _config(), store))

    assert result.channel_conflict == 999
    assert result.changed is False


def test_apply_exclusive_colour_swaps_previous(tmp_path: Path) -> None:
    red = FakeRole(555, "Red")
    blue = FakeRole(556, "Blue")
    guild = FakeGuild(GUILD_ID, roles=(red, blue))
    member = FakeMember(7, guild, roles=(red,))
    message = FakeMessage(424)

    asyncio.run(apply_exclusive_colour(member, message, 556, others={555: "🔴"}))

    assert [r.id for r in member.added] == [556]
    assert [r.id for r in member.removed] == [555]
    assert message.removed_reactions == [("🔴", 7)]


def test_apply_exclusive_colour_no_others(tmp_path: Path) -> None:
    guild = FakeGuild(GUILD_ID, roles=(FakeRole(556, "Blue"),))
    member = FakeMember(7, guild)
    message = FakeMessage(424)

    asyncio.run(apply_exclusive_colour(member, message, 556, others={}))

    assert [r.id for r in member.added] == [556]
    assert member.removed == []
    assert message.removed_reactions == []
