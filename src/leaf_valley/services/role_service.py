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
ROLE_CLEAR_REASON = "Leaf Valley: weekly reset"
ROLE_ASSIGN_REASON = "Leaf Valley: reaction signup"
ROLE_REMOVE_REASON = "Leaf Valley: reaction removed"


@dataclass
class RoleClearResult:
    """Per-run summary of removing managed roles from members (weekly reset)."""

    roles_cleared: int = 0
    members_affected: int = 0
    # Set once Discord denies a removal; remaining members/roles are skipped.
    forbidden: bool = False


@dataclass
class RoleSyncResult:
    """Per-run summary of what happened to each item's role, by role name."""

    created: list[str] = field(default_factory=list)
    adopted: list[str] = field(default_factory=list)
    existing: list[str] = field(default_factory=list)
    # Existing/adopted roles that weren't @mentionable and were fixed so pings notify.
    made_mentionable: list[str] = field(default_factory=list)
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
                await _ensure_mentionable(roles_by_id[existing_id], result)
                result.existing.append(item.role_name)
                continue

            adopted = roles_by_name.get(item.role_name)
            if adopted is not None:
                await _ensure_mentionable(adopted, result)
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

    log.info(
        "Role sync in guild %s: created=%d adopted=%d existing=%d.",
        guild.id,
        len(result.created),
        len(result.adopted),
        len(result.existing),
    )
    return result


async def _ensure_mentionable(role: discord.Role, result: RoleSyncResult) -> None:
    """Make a managed role @mentionable so pinging it notifies its members.

    Freshly created roles already set this, but roles that were adopted or created
    before this was enforced may not be mentionable - and Discord silently skips the
    notification when a non-mentionable role is pinged. A permission denial is logged
    and swallowed so it never blocks role linking.
    """
    if role.mentionable:
        return
    try:
        await role.edit(mentionable=True, reason=ROLE_CREATE_REASON)
    except discord.Forbidden:
        log.error(
            "Missing 'Manage Roles' (or role hierarchy too low) in guild %s; "
            "cannot make role %r mentionable.",
            role.guild.id,
            role.name,
        )
        return
    result.made_mentionable.append(role.name)


async def clear_all(guild: discord.Guild, role_ids: set[int]) -> RoleClearResult:
    """Remove every managed item role from all members that hold it.

    Iterates the given role IDs (typically ``store.managed_role_ids(guild.id)``),
    stripping each role from its current holders. Roles no longer present in the
    guild are skipped. ``members_affected`` counts distinct members touched, so a
    member prepping several items is counted once. Stops the first time Discord
    denies a removal, since that always means the bot's role sits too low or it
    lacks Manage Roles; state is left untouched either way (roles aren't deleted).
    """
    result = RoleClearResult()
    affected: set[int] = set()

    for role_id in role_ids:
        role = guild.get_role(role_id)
        if role is None:
            continue
        # Snapshot holders: remove_roles mutates role.members mid-iteration.
        for member in list(role.members):
            try:
                await member.remove_roles(role, reason=ROLE_CLEAR_REASON)
            except discord.Forbidden:
                log.error(
                    "Missing 'Manage Roles' (or role hierarchy too low) in guild %s; "
                    "cannot remove role %r.",
                    guild.id,
                    role.name,
                )
                result.forbidden = True
                return result
            affected.add(member.id)
        result.roles_cleared += 1

    result.members_affected = len(affected)
    log.info(
        "Cleared %d role(s) from %d member(s) in guild %s.",
        result.roles_cleared,
        result.members_affected,
        guild.id,
    )
    return result


async def assign_role(member: discord.Member, role_id: int) -> bool:
    """Add the managed role ``role_id`` to ``member``; return True on success.

    Called by the reaction-add listener. A role missing from the guild (deleted
    since setup) is a no-op returning False. If the member already holds the role,
    short-circuits without an API call or log line (see PLAN.md §8). A permission
    denial is logged and returns False rather than raising, so one bad reaction
    can't crash the listener.
    """
    role = member.guild.get_role(role_id)
    if role is None:
        return False
    if any(r.id == role_id for r in member.roles):
        return True
    try:
        await member.add_roles(role, reason=ROLE_ASSIGN_REASON)
    except discord.Forbidden:
        log.error(
            "Missing 'Manage Roles' (or role hierarchy too low) in guild %s; "
            "cannot add role %r to member %s.",
            member.guild.id,
            role.name,
            member.id,
        )
        return False
    log.info(
        "Assigned role %r to member %s in guild %s.",
        role.name,
        member.id,
        member.guild.id,
    )
    return True


async def remove_role(member: discord.Member, role_id: int) -> bool:
    """Remove the managed role ``role_id`` from ``member``; return True on success.

    Called by the reaction-remove listener. Mirrors ``assign_role``: an unknown role
    is a no-op returning False, a member who doesn't hold the role short-circuits
    without an API call or log line (see PLAN.md §8), and a permission denial is
    logged and returns False instead of raising.
    """
    role = member.guild.get_role(role_id)
    if role is None:
        return False
    if not any(r.id == role_id for r in member.roles):
        return True
    try:
        await member.remove_roles(role, reason=ROLE_REMOVE_REASON)
    except discord.Forbidden:
        log.error(
            "Missing 'Manage Roles' (or role hierarchy too low) in guild %s; "
            "cannot remove role %r from member %s.",
            member.guild.id,
            role.name,
            member.id,
        )
        return False
    log.info(
        "Removed role %r from member %s in guild %s.",
        role.name,
        member.id,
        member.guild.id,
    )
    return True
