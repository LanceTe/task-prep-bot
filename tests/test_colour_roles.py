from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import discord

from leaf_valley.cogs.colour_roles import ColourRoles
from leaf_valley.config.schema import ColourConfig, ColourRole
from leaf_valley.storage.state_store import StateStore

# guild 111, colour board message 424 in channel 999.
# 🔴 -> red role 555, 🔵 -> blue role 556.
GUILD_ID = 111
MESSAGE_ID = 424
CHANNEL_ID = 999
RED_ROLE = 555
BLUE_ROLE = 556
BOT_ID = 42


class FakeRole:
    def __init__(self, id: int, name: str) -> None:
        self.id = id
        self.name = name


class FakeMember:
    def __init__(
        self, id: int, guild: FakeGuild, roles: tuple[FakeRole, ...] = ()
    ) -> None:
        self.id = id
        self.guild = guild
        self.display_name = f"member-{id}"
        self.roles = list(roles)
        self.added: list[FakeRole] = []
        self.removed: list[FakeRole] = []

    async def add_roles(self, role: FakeRole, *, reason: str) -> None:
        self.added.append(role)

    async def remove_roles(self, role: FakeRole, *, reason: str) -> None:
        self.removed.append(role)


class FakeGuild:
    def __init__(self, id: int, roles: tuple[FakeRole, ...] = ()) -> None:
        self.id = id
        self._roles = {role.id: role for role in roles}
        self._members: dict[int, FakeMember] = {}

    def add_member(self, member: FakeMember) -> None:
        self._members[member.id] = member

    def get_role(self, role_id: int) -> FakeRole | None:
        return self._roles.get(role_id)

    def get_member(self, user_id: int) -> FakeMember | None:
        return self._members.get(user_id)


class FakeMessage:
    def __init__(self, id: int) -> None:
        self.id = id
        self.removed_reactions: list[tuple[str, int]] = []

    async def remove_reaction(self, emoji: str, member: FakeMember) -> None:
        self.removed_reactions.append((emoji, member.id))


class FakeChannel(discord.abc.Messageable):
    def __init__(self, id: int, message: FakeMessage) -> None:
        self.id = id
        self._message = message

    async def fetch_message(self, message_id: int) -> FakeMessage:
        if message_id == self._message.id:
            return self._message
        resp = SimpleNamespace(status=404, reason="Not Found")
        raise discord.NotFound(resp, "unknown message")


class FakeBot:
    def __init__(
        self, state: StateStore, guild: FakeGuild, channel: FakeChannel
    ) -> None:
        self.state = state
        self.user = SimpleNamespace(id=BOT_ID)
        self.colour_config = _config()
        self._guild = guild
        self._channel = channel

    def get_guild(self, guild_id: int) -> FakeGuild | None:
        return self._guild if guild_id == self._guild.id else None

    def get_channel(self, channel_id: int) -> FakeChannel | None:
        return self._channel if channel_id == self._channel.id else None


def _config() -> ColourConfig:
    return ColourConfig(
        colours=(
            ColourRole(key="red", role_name="Red", emoji="🔴", colour=0xE74C3C),
            ColourRole(key="blue", role_name="Blue", emoji="🔵", colour=0x3498DB),
        )
    )


def _payload(
    *,
    user_id: int,
    guild_id: int | None = GUILD_ID,
    message_id: int = MESSAGE_ID,
    channel_id: int = CHANNEL_ID,
    emoji_id: int | None = None,
    emoji_name: str = "🔴",
    member: FakeMember | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        user_id=user_id,
        guild_id=guild_id,
        message_id=message_id,
        channel_id=channel_id,
        emoji=SimpleNamespace(id=emoji_id, name=emoji_name),
        member=member,
    )


def _store(tmp_path: Path) -> StateStore:
    store = StateStore.load(tmp_path / "state.json")
    store.set_colour_channel_id(GUILD_ID, CHANNEL_ID)
    store.set_colour_message_id(GUILD_ID, MESSAGE_ID)
    store.set_colour_role_id(GUILD_ID, "red", RED_ROLE)
    store.set_colour_role_id(GUILD_ID, "blue", BLUE_ROLE)
    return store


def _fixture(
    tmp_path: Path, member_roles: tuple[FakeRole, ...] = ()
) -> tuple[ColourRoles, FakeMember, FakeMessage]:
    red = FakeRole(RED_ROLE, "Red")
    blue = FakeRole(BLUE_ROLE, "Blue")
    guild = FakeGuild(GUILD_ID, roles=(red, blue))
    member = FakeMember(7, guild, roles=member_roles)
    guild.add_member(member)
    message = FakeMessage(MESSAGE_ID)
    channel = FakeChannel(CHANNEL_ID, message)
    cog = ColourRoles(FakeBot(_store(tmp_path), guild, channel))
    return cog, member, message


def test_add_grants_colour_when_none_held(tmp_path: Path) -> None:
    cog, member, message = _fixture(tmp_path)
    payload = _payload(user_id=member.id, emoji_name="🔴", member=member)

    asyncio.run(cog.on_raw_reaction_add(payload))

    assert [r.id for r in member.added] == [RED_ROLE]
    assert member.removed == []
    assert message.removed_reactions == []


def test_add_swaps_previous_colour(tmp_path: Path) -> None:
    blue = FakeRole(BLUE_ROLE, "Blue")
    cog, member, message = _fixture(tmp_path, member_roles=(blue,))
    payload = _payload(user_id=member.id, emoji_name="🔴", member=member)

    asyncio.run(cog.on_raw_reaction_add(payload))

    assert [r.id for r in member.added] == [RED_ROLE]
    assert [r.id for r in member.removed] == [BLUE_ROLE]
    # The old colour's reaction is cleared from the board for this member.
    assert message.removed_reactions == [("🔵", member.id)]


def test_add_ignores_bot_own_reaction(tmp_path: Path) -> None:
    cog, member, _message = _fixture(tmp_path)
    payload = _payload(user_id=BOT_ID, member=member)

    asyncio.run(cog.on_raw_reaction_add(payload))

    assert member.added == []


def test_add_ignores_dm(tmp_path: Path) -> None:
    cog, member, _message = _fixture(tmp_path)
    payload = _payload(user_id=member.id, guild_id=None, member=member)

    asyncio.run(cog.on_raw_reaction_add(payload))

    assert member.added == []


def test_add_ignores_other_message(tmp_path: Path) -> None:
    cog, member, _message = _fixture(tmp_path)
    payload = _payload(user_id=member.id, message_id=999999, member=member)

    asyncio.run(cog.on_raw_reaction_add(payload))

    assert member.added == []


def test_add_ignores_custom_emoji(tmp_path: Path) -> None:
    cog, member, _message = _fixture(tmp_path)
    payload = _payload(user_id=member.id, emoji_id=123, member=member)

    asyncio.run(cog.on_raw_reaction_add(payload))

    assert member.added == []


def test_add_ignores_unknown_colour_emoji(tmp_path: Path) -> None:
    cog, member, _message = _fixture(tmp_path)
    payload = _payload(user_id=member.id, emoji_name="🟤", member=member)

    asyncio.run(cog.on_raw_reaction_add(payload))

    assert member.added == []


def test_remove_strips_colour_role(tmp_path: Path) -> None:
    red = FakeRole(RED_ROLE, "Red")
    cog, member, _message = _fixture(tmp_path, member_roles=(red,))
    payload = _payload(user_id=member.id, emoji_name="🔴", member=None)

    asyncio.run(cog.on_raw_reaction_remove(payload))

    assert [r.id for r in member.removed] == [RED_ROLE]


def test_remove_ignores_unknown_colour_emoji(tmp_path: Path) -> None:
    cog, member, _message = _fixture(tmp_path)
    payload = _payload(user_id=member.id, emoji_name="🟤", member=None)

    asyncio.run(cog.on_raw_reaction_remove(payload))

    assert member.removed == []
