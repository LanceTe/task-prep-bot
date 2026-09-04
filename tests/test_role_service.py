from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import discord

from leaf_valley.config.schema import Factory, FactoryConfig, Item
from leaf_valley.services.role_service import create_missing_roles
from leaf_valley.storage.state_store import StateStore


class FakeRole:
    def __init__(self, id: int, name: str) -> None:
        self.id = id
        self.name = name


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
        self.roles = list(roles)
        self.forbid_create = forbid_create
        self.created: list[tuple[str, bool, str]] = []
        # Offset by guild id so distinct guilds mint distinct role IDs, as Discord does.
        self._next_id = id * 1000

    async def create_role(self, *, name: str, mentionable: bool, reason: str) -> FakeRole:
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
