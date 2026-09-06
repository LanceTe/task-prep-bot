from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import discord

from leaf_valley.config.schema import Factory, FactoryConfig, Item
from leaf_valley.services.role_service import (
    assign_role,
    clear_all,
    create_missing_roles,
    remove_role,
)
from leaf_valley.storage.state_store import StateStore


class FakeRole:
    def __init__(self, id: int, name: str, *, mentionable: bool = True) -> None:
        self.id = id
        self.name = name
        self.mentionable = mentionable
        self.guild: FakeGuild | None = None
        # Members currently holding this role (mutated by FakeMember.remove_roles).
        self.members: list[FakeMember] = []

    async def edit(self, *, mentionable: bool, reason: str) -> None:
        self.mentionable = mentionable


class FakeMember:
    def __init__(self, id: int, *, forbid_remove: bool = False) -> None:
        self.id = id
        self.forbid_remove = forbid_remove
        self.removed: list[FakeRole] = []

    async def remove_roles(self, role: FakeRole, *, reason: str) -> None:
        if self.forbid_remove:
            resp = SimpleNamespace(status=403, reason="Forbidden")
            raise discord.Forbidden(resp, "missing Manage Roles")
        self.removed.append(role)
        if self in role.members:
            role.members.remove(self)


class FakeGuild:
    """Minimal duck-typed stand-in for discord.Guild used by role_service."""

    def __init__(
        self,
        id: int,
        roles: tuple[FakeRole, ...] = (),
        *,
        forbid_create: bool = False,
    ) -> None:
        self.id = id
        self.name = f"guild-{id}"
        self.roles = list(roles)
        self.forbid_create = forbid_create
        self.created: list[tuple[str, bool, str]] = []
        # Offset by guild id so distinct guilds mint distinct role IDs, as Discord does.
        self._next_id = id * 1000

    def get_role(self, role_id: int) -> FakeRole | None:
        return next((role for role in self.roles if role.id == role_id), None)

    async def create_role(
        self, *, name: str, mentionable: bool, reason: str
    ) -> FakeRole:
        if self.forbid_create:
            resp = SimpleNamespace(status=403, reason="Forbidden")
            raise discord.Forbidden(resp, "missing Manage Roles")
        self._next_id += 1
        role = FakeRole(self._next_id, name)
        self.roles.append(role)
        self.created.append((name, mentionable, reason))
        return role


def _config() -> FactoryConfig:
    return FactoryConfig(
        factories=(
            Factory(
                key="dairy",
                name="Dairy",
                image=None,
                items=(
                    Item(key="cheese", role_name="cheese", emoji=":cheese:"),
                    Item(key="cream", role_name="cream", emoji=":cream:"),
                ),
            ),
        )
    )


def _store(tmp_path: Path) -> StateStore:
    return StateStore.load(tmp_path / "state.json")


def test_creates_all_missing_roles(tmp_path: Path) -> None:
    guild = FakeGuild(111)
    store = _store(tmp_path)

    result = asyncio.run(create_missing_roles(guild, _config(), store))

    assert result.created == ["cheese", "cream"]
    assert result.adopted == []
    assert result.existing == []
    assert result.forbidden is False
    assert result.changed is True
    # Roles were created as mentionable, and their IDs recorded in state.
    assert all(mentionable for _, mentionable, _ in guild.created)
    assert store.get_role_id(111, "dairy", "cheese") is not None
    assert store.get_role_id(111, "dairy", "cream") is not None


def test_existing_state_is_left_untouched(tmp_path: Path) -> None:
    guild = FakeGuild(111, roles=(FakeRole(333, "cheese"),))
    store = _store(tmp_path)
    store.set_role_id(111, "dairy", "cheese", 333)

    result = asyncio.run(create_missing_roles(guild, _config(), store))

    assert result.existing == ["cheese"]
    assert result.created == ["cream"]
    # cheese must not have been recreated.
    assert [name for name, _, _ in guild.created] == ["cream"]
    assert store.get_role_id(111, "dairy", "cheese") == 333


def test_adopts_role_by_name_when_state_missing(tmp_path: Path) -> None:
    guild = FakeGuild(111, roles=(FakeRole(500, "cheese"),))
    store = _store(tmp_path)

    result = asyncio.run(create_missing_roles(guild, _config(), store))

    assert result.adopted == ["cheese"]
    assert result.created == ["cream"]
    # Adoption reuses the existing role's ID rather than creating a new one.
    assert store.get_role_id(111, "dairy", "cheese") == 500
    assert [name for name, _, _ in guild.created] == ["cream"]


def test_adopts_when_state_id_is_stale(tmp_path: Path) -> None:
    # State points at a role that no longer exists, but the name is still present.
    guild = FakeGuild(111, roles=(FakeRole(500, "cheese"),))
    store = _store(tmp_path)
    store.set_role_id(111, "dairy", "cheese", 999)

    result = asyncio.run(create_missing_roles(guild, _config(), store))

    assert result.adopted == ["cheese"]
    assert store.get_role_id(111, "dairy", "cheese") == 500


def test_adopted_role_is_made_mentionable(tmp_path: Path) -> None:
    # A pre-existing role with mentions disabled must be fixed so pings notify.
    silent = FakeRole(500, "cheese", mentionable=False)
    guild = FakeGuild(111, roles=(silent,))
    store = _store(tmp_path)

    result = asyncio.run(create_missing_roles(guild, _config(), store))

    assert result.adopted == ["cheese"]
    assert result.made_mentionable == ["cheese"]
    assert silent.mentionable is True


def test_existing_role_is_made_mentionable(tmp_path: Path) -> None:
    # A role already linked in state but with mentions disabled is fixed in place.
    silent = FakeRole(333, "cheese", mentionable=False)
    guild = FakeGuild(111, roles=(silent,))
    store = _store(tmp_path)
    store.set_role_id(111, "dairy", "cheese", 333)

    result = asyncio.run(create_missing_roles(guild, _config(), store))

    assert result.existing == ["cheese"]
    assert result.made_mentionable == ["cheese"]
    assert silent.mentionable is True


def test_already_mentionable_role_is_left_alone(tmp_path: Path) -> None:
    role = FakeRole(333, "cheese", mentionable=True)
    guild = FakeGuild(111, roles=(role,))
    store = _store(tmp_path)
    store.set_role_id(111, "dairy", "cheese", 333)

    result = asyncio.run(create_missing_roles(guild, _config(), store))

    assert result.made_mentionable == []


def test_forbidden_stops_creation_but_still_adopts(tmp_path: Path) -> None:
    guild = FakeGuild(111, roles=(FakeRole(500, "cheese"),), forbid_create=True)
    store = _store(tmp_path)

    result = asyncio.run(create_missing_roles(guild, _config(), store))

    assert result.adopted == ["cheese"]
    assert result.created == []
    assert result.forbidden is True
    # The adoption still counts as a change worth saving.
    assert result.changed is True
    assert store.get_role_id(111, "dairy", "cheese") == 500
    assert store.get_role_id(111, "dairy", "cream") is None


def test_multi_guild_isolation(tmp_path: Path) -> None:
    store = _store(tmp_path)
    guild_a = FakeGuild(111)
    guild_b = FakeGuild(222)

    asyncio.run(create_missing_roles(guild_a, _config(), store))
    asyncio.run(create_missing_roles(guild_b, _config(), store))

    a_cheese = store.get_role_id(111, "dairy", "cheese")
    b_cheese = store.get_role_id(222, "dairy", "cheese")
    assert a_cheese is not None and b_cheese is not None
    assert a_cheese != b_cheese


def test_clear_all_removes_managed_roles_from_members() -> None:
    cheese = FakeRole(333, "cheese")
    cream = FakeRole(334, "cream")
    alice = FakeMember(1)
    bob = FakeMember(2)
    # Alice preps both items; Bob only cheese.
    cheese.members = [alice, bob]
    cream.members = [alice]
    guild = FakeGuild(111, roles=(cheese, cream))

    result = asyncio.run(clear_all(guild, {333, 334}))

    assert result.roles_cleared == 2
    # Alice is counted once despite holding two roles.
    assert result.members_affected == 2
    assert result.forbidden is False
    assert cheese.members == []
    assert cream.members == []


def test_clear_all_skips_unknown_roles() -> None:
    cheese = FakeRole(333, "cheese")
    cheese.members = [FakeMember(1)]
    guild = FakeGuild(111, roles=(cheese,))

    # 999 no longer exists in the guild; it should be silently skipped.
    result = asyncio.run(clear_all(guild, {333, 999}))

    assert result.roles_cleared == 1
    assert result.members_affected == 1


def test_clear_all_counts_empty_roles() -> None:
    empty = FakeRole(333, "cheese")
    guild = FakeGuild(111, roles=(empty,))

    result = asyncio.run(clear_all(guild, {333}))

    assert result.roles_cleared == 1
    assert result.members_affected == 0


def test_clear_all_forbidden_stops_and_flags() -> None:
    cheese = FakeRole(333, "cheese")
    cheese.members = [FakeMember(1, forbid_remove=True)]
    guild = FakeGuild(111, roles=(cheese,))

    result = asyncio.run(clear_all(guild, {333}))

    assert result.forbidden is True
    assert result.roles_cleared == 0
    assert result.members_affected == 0


class FakeSignupMember:
    """A member that tracks role add/remove, backed by a guild for role lookup."""

    def __init__(
        self,
        id: int,
        guild: FakeGuild,
        *,
        roles: tuple[FakeRole, ...] = (),
        forbid_add: bool = False,
        forbid_remove: bool = False,
    ) -> None:
        self.id = id
        self.guild = guild
        self.display_name = f"member-{id}"
        self.roles = list(roles)
        self.forbid_add = forbid_add
        self.forbid_remove = forbid_remove
        self.added: list[FakeRole] = []
        self.removed: list[FakeRole] = []

    async def add_roles(self, role: FakeRole, *, reason: str) -> None:
        if self.forbid_add:
            resp = SimpleNamespace(status=403, reason="Forbidden")
            raise discord.Forbidden(resp, "missing Manage Roles")
        self.added.append(role)
        if role not in self.roles:
            self.roles.append(role)

    async def remove_roles(self, role: FakeRole, *, reason: str) -> None:
        if self.forbid_remove:
            resp = SimpleNamespace(status=403, reason="Forbidden")
            raise discord.Forbidden(resp, "missing Manage Roles")
        self.removed.append(role)
        if role in self.roles:
            self.roles.remove(role)


def test_assign_role_adds_managed_role() -> None:
    cheese = FakeRole(333, "cheese")
    guild = FakeGuild(111, roles=(cheese,))
    member = FakeSignupMember(1, guild)

    ok = asyncio.run(assign_role(member, 333))

    assert ok is True
    assert member.added == [cheese]


def test_assign_role_unknown_role_is_noop() -> None:
    guild = FakeGuild(111)
    member = FakeSignupMember(1, guild)

    ok = asyncio.run(assign_role(member, 999))

    assert ok is False
    assert member.added == []


def test_assign_role_forbidden_returns_false() -> None:
    cheese = FakeRole(333, "cheese")
    guild = FakeGuild(111, roles=(cheese,))
    member = FakeSignupMember(1, guild, forbid_add=True)

    ok = asyncio.run(assign_role(member, 333))

    assert ok is False
    assert member.added == []


def test_remove_role_removes_managed_role() -> None:
    cheese = FakeRole(333, "cheese")
    guild = FakeGuild(111, roles=(cheese,))
    member = FakeSignupMember(1, guild, roles=(cheese,))

    ok = asyncio.run(remove_role(member, 333))

    assert ok is True
    assert member.removed == [cheese]


def test_remove_role_unknown_role_is_noop() -> None:
    guild = FakeGuild(111)
    member = FakeSignupMember(1, guild)

    ok = asyncio.run(remove_role(member, 999))

    assert ok is False
    assert member.removed == []


def test_remove_role_forbidden_returns_false() -> None:
    cheese = FakeRole(333, "cheese")
    guild = FakeGuild(111, roles=(cheese,))
    member = FakeSignupMember(1, guild, roles=(cheese,), forbid_remove=True)

    ok = asyncio.run(remove_role(member, 333))

    assert ok is False
    assert member.removed == []


def test_assign_role_already_held_short_circuits() -> None:
    cheese = FakeRole(333, "cheese")
    guild = FakeGuild(111, roles=(cheese,))
    member = FakeSignupMember(1, guild, roles=(cheese,))

    ok = asyncio.run(assign_role(member, 333))

    assert ok is True
    assert member.added == []


def test_remove_role_not_held_short_circuits() -> None:
    cheese = FakeRole(333, "cheese")
    guild = FakeGuild(111, roles=(cheese,))
    member = FakeSignupMember(1, guild)  # role exists in guild, not on member

    ok = asyncio.run(remove_role(member, 333))

    assert ok is True
    assert member.removed == []
