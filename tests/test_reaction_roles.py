from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from leaf_valley.cogs.reaction_roles import ReactionRoles
from leaf_valley.storage.state_store import StateStore

# In this module: guild 111, message 222, cheese emoji 444 -> cheese role 333.
GUILD_ID = 111
MESSAGE_ID = 222
EMOJI_ID = 444
ROLE_ID = 333
BOT_ID = 42


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
        self._roles = {role.id: role for role in roles}
        self._members: dict[int, FakeMember] = {}

    def add_member(self, member: FakeMember) -> None:
        self._members[member.id] = member

    def get_role(self, role_id: int) -> FakeRole | None:
        return self._roles.get(role_id)

    def get_member(self, user_id: int) -> FakeMember | None:
        return self._members.get(user_id)


class FakeBot:
    def __init__(self, state: StateStore, guild: FakeGuild) -> None:
        self.state = state
        self.user = SimpleNamespace(id=BOT_ID)
        self._guild = guild

    def get_guild(self, guild_id: int) -> FakeGuild | None:
        return self._guild if guild_id == self._guild.id else None


def _payload(
    *,
    user_id: int,
    guild_id: int | None = GUILD_ID,
    message_id: int = MESSAGE_ID,
    emoji_id: int | None = EMOJI_ID,
    member: FakeMember | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        user_id=user_id,
        guild_id=guild_id,
        message_id=message_id,
        emoji=SimpleNamespace(id=emoji_id),
        member=member,
    )


def _wired_store(tmp_path: Path) -> StateStore:
    store = StateStore.load(tmp_path / "state.json")
    store.set_message_id(GUILD_ID, "milk_factory", MESSAGE_ID)
    store.set_role_id(GUILD_ID, "milk_factory", "cheese", ROLE_ID)
    store.set_emoji_id(GUILD_ID, "milk_factory", "cheese", EMOJI_ID)
    return store


def _fixture(
    tmp_path: Path, *, member_roles: tuple[FakeRole, ...] = ()
) -> tuple[ReactionRoles, FakeMember]:
    cheese_role = FakeRole(ROLE_ID, "cheese")
    guild = FakeGuild(GUILD_ID, roles=(cheese_role,))
    member = FakeMember(7, guild, roles=member_roles)
    guild.add_member(member)
    cog = ReactionRoles(FakeBot(_wired_store(tmp_path), guild))
    return cog, member


def test_add_grants_role_using_payload_member(tmp_path: Path) -> None:
    cog, member = _fixture(tmp_path)
    payload = _payload(user_id=member.id, member=member)

    asyncio.run(cog.on_raw_reaction_add(payload))

    assert [role.id for role in member.added] == [ROLE_ID]


def test_add_resolves_member_when_payload_member_missing(tmp_path: Path) -> None:
    cog, member = _fixture(tmp_path)
    payload = _payload(user_id=member.id, member=None)

    asyncio.run(cog.on_raw_reaction_add(payload))

    assert [role.id for role in member.added] == [ROLE_ID]


def test_add_ignores_bot_own_reaction(tmp_path: Path) -> None:
    cog, member = _fixture(tmp_path)
    payload = _payload(user_id=BOT_ID, member=member)

    asyncio.run(cog.on_raw_reaction_add(payload))

    assert member.added == []


def test_add_ignores_dm_reaction(tmp_path: Path) -> None:
    cog, member = _fixture(tmp_path)
    payload = _payload(user_id=member.id, guild_id=None, member=member)

    asyncio.run(cog.on_raw_reaction_add(payload))

    assert member.added == []


def test_add_ignores_unknown_emoji(tmp_path: Path) -> None:
    cog, member = _fixture(tmp_path)
    payload = _payload(user_id=member.id, emoji_id=999, member=member)

    asyncio.run(cog.on_raw_reaction_add(payload))

    assert member.added == []


def test_add_ignores_unicode_emoji(tmp_path: Path) -> None:
    cog, member = _fixture(tmp_path)
    payload = _payload(user_id=member.id, emoji_id=None, member=member)

    asyncio.run(cog.on_raw_reaction_add(payload))

    assert member.added == []


def test_add_ignores_reaction_on_other_message(tmp_path: Path) -> None:
    cog, member = _fixture(tmp_path)
    payload = _payload(user_id=member.id, message_id=555, member=member)

    asyncio.run(cog.on_raw_reaction_add(payload))

    assert member.added == []


def test_remove_strips_role(tmp_path: Path) -> None:
    cheese = FakeRole(ROLE_ID, "cheese")
    cog, member = _fixture(tmp_path, member_roles=(cheese,))
    # Remove events never carry payload.member, so the cog must resolve it.
    payload = _payload(user_id=member.id, member=None)

    asyncio.run(cog.on_raw_reaction_remove(payload))

    assert [role.id for role in member.removed] == [ROLE_ID]


def test_remove_ignores_unknown_emoji(tmp_path: Path) -> None:
    cog, member = _fixture(tmp_path)
    payload = _payload(user_id=member.id, emoji_id=999, member=None)

    asyncio.run(cog.on_raw_reaction_remove(payload))

    assert member.removed == []


def test_remove_ignores_unknown_member(tmp_path: Path) -> None:
    cog, member = _fixture(tmp_path)
    # A user id the guild doesn't know about resolves to no member; no crash.
    payload = _payload(user_id=999, member=None)

    asyncio.run(cog.on_raw_reaction_remove(payload))

    assert member.removed == []
