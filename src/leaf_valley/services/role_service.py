"""Create/link the Discord roles backing each config item, idempotently.

Roles map 1:1 to items in factories.yaml. For each item this ensures a guild role
exists and its ID is recorded in the state store, so reaction events can later map
(message, emoji) -> role.

Each item resolves to one of three outcomes:
  * existing - state already points at a role still present in the guild; untouched.
  * adopted  - state had no (or a stale) ID but a role with the same name exists;
               its ID is reused. This recovers gracefully from a lost state.json.
  * created  - no matching role anywhere; a new mentionable role is created.

Multi-guild: every lookup/write is scoped by guild.id, so each server keeps an
independent set of role IDs.

The service mutates the in-memory StateStore but never saves it; the caller persists
once via StateStore.save() so the storage layer stays the single writer.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import discord

if TYPE_CHECKING:
    from leaf_valley.config.schema import FactoryConfig
    from leaf_valley.storage.state_store import StateStore

log = logging.getLogger(__name__)

ROLE_CREATE_REASON = "Leaf Valley: item preparation role"


@dataclass
class RoleSyncResult:
    """Per-run summary of what happened to each item's role, by role name."""

    created: list[str] = field(default_factory=list)
    adopted: list[str] = field(default_factory=list)
    existing: list[str] = field(default_factory=list)
    # Set once the bot is denied create permission; remaining creates are skipped.
    forbidden: bool = False

    @property
    def changed(self) -> bool:
        """True when the state store gained or repointed a role ID (needs saving)."""
        return bool(self.created or self.adopted)


async def create_missing_roles(
    guild: discord.Guild,
    config: FactoryConfig,
    store: StateStore,
) -> RoleSyncResult:
    """Ensure every config item has a role in ``guild`` recorded in ``store``.

    Idempotent: re-running only creates/adopts what is missing. Aborts further
    creations (but keeps adopting) the first time Discord denies role creation,
    since that always means the bot lacks the Manage Roles permission.
    """
    result = RoleSyncResult()

    roles_by_id = {role.id: role for role in guild.roles}
    # First match wins when duplicate names exist (Discord permits them).
    roles_by_name: dict[str, discord.Role] = {}
    for role in guild.roles:
        roles_by_name.setdefault(role.name, role)

    for factory in config.factories:
        for item in factory.items:
            existing_id = store.get_role_id(guild.id, factory.key, item.key)
            if existing_id is not None and existing_id in roles_by_id:
                result.existing.append(item.role_name)
                continue

            adopted = roles_by_name.get(item.role_name)
            if adopted is not None:
                store.set_role_id(guild.id, factory.key, item.key, adopted.id)
                result.adopted.append(item.role_name)
                continue

            if result.forbidden:
                continue

            try:
                role = await guild.create_role(
                    name=item.role_name,
                    mentionable=True,
                    reason=ROLE_CREATE_REASON,
                )
            except discord.Forbidden:
                log.error(
                    "Missing 'Manage Roles' in guild %s; cannot create role %r.",
                    guild.id,
                    item.role_name,
                )
                result.forbidden = True
                continue

            roles_by_id[role.id] = role
            roles_by_name.setdefault(role.name, role)
            store.set_role_id(guild.id, factory.key, item.key, role.id)
            result.created.append(item.role_name)

    return result
