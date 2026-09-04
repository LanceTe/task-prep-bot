from __future__ import annotations

from pathlib import Path

import pytest

from leaf_valley.storage.state_store import StateError, StateStore


def test_missing_file_yields_empty_state(tmp_path: Path) -> None:
    store = StateStore.load(tmp_path / "state.json")
    assert store.guilds == {}
    assert store.get_guild(111) is None


def test_save_creates_data_dir_and_roundtrips(tmp_path: Path) -> None:
    path = tmp_path / "data" / "state.json"
    store = StateStore.load(path)

    store.set_channel_id(111, 777)
    store.set_message_id(111, "milk_factory", 222)
    store.set_role_id(111, "milk_factory", "cheese", 333)
    store.set_emoji_id(111, "milk_factory", "cheese", 444)
    store.save()

    assert path.is_file()

    reloaded = StateStore.load(path)
    assert reloaded.get_channel_id(111) == 777
    assert reloaded.factory_key_for_message(111, 222) == "milk_factory"
    assert reloaded.get_role_id(111, "milk_factory", "cheese") == 333
    guild = reloaded.get_guild(111)
    assert guild is not None
    assert guild.factories["milk_factory"].items["cheese"].emoji_id == 444


def test_reset_setup_clears_channel_and_messages_but_keeps_roles(
    tmp_path: Path,
) -> None:
    store = StateStore.load(tmp_path / "state.json")
    store.set_channel_id(111, 777)
    store.set_message_id(111, "milk_factory", 222)
    store.set_role_id(111, "milk_factory", "cheese", 333)

    store.reset_setup(111)

    assert store.get_channel_id(111) is None
    assert store.get_message_id(111, "milk_factory") is None
    # Roles survive a teardown so signups (held as roles) are preserved.
    assert store.get_role_id(111, "milk_factory", "cheese") == 333


def test_multiple_guilds_are_isolated(tmp_path: Path) -> None:
    store = StateStore.load(tmp_path / "state.json")
    store.set_role_id(111, "milk_factory", "cheese", 333)
    store.set_role_id(222, "milk_factory", "cheese", 999)
    store.save()

    reloaded = StateStore.load(tmp_path / "state.json")
    assert reloaded.get_role_id(111, "milk_factory", "cheese") == 333
    assert reloaded.get_role_id(222, "milk_factory", "cheese") == 999


def test_colour_state_roundtrips(tmp_path: Path) -> None:
    path = tmp_path / "data" / "state.json"
    store = StateStore.load(path)

    store.set_colour_channel_id(111, 999)
    store.set_colour_message_id(111, 424)
    store.set_colour_role_id(111, "red", 555)
    store.set_colour_role_id(111, "blue", 556)
    store.save()

    reloaded = StateStore.load(path)
    assert reloaded.get_colour_channel_id(111) == 999
    assert reloaded.get_colour_message_id(111) == 424
    assert reloaded.get_colour_role_id(111, "red") == 555
    assert reloaded.managed_colour_role_ids(111) == {555, 556}


def test_colour_reads_on_unknown_guild_return_defaults(tmp_path: Path) -> None:
    store = StateStore.load(tmp_path / "state.json")
    assert store.get_colour_channel_id(111) is None
    assert store.get_colour_message_id(111) is None
    assert store.get_colour_role_id(111, "red") is None
    assert store.managed_colour_role_ids(111) == set()


def test_managed_role_ids_collects_across_factories(tmp_path: Path) -> None:
    store = StateStore.load(tmp_path / "state.json")
    store.set_role_id(111, "milk_factory", "cheese", 333)
    store.set_role_id(111, "milk_factory", "cream", 334)
    store.set_role_id(111, "bakery", "bread", 335)
    # An item with no role yet must not contribute a None.
    store.set_message_id(111, "bakery", 500)

    assert store.managed_role_ids(111) == {333, 334, 335}


def test_reads_never_create_entries(tmp_path: Path) -> None:
    store = StateStore.load(tmp_path / "state.json")

    assert store.factory_key_for_message(111, 999) is None
    assert store.get_role_id(111, "milk_factory", "cheese") is None
    assert store.get_channel_id(111) is None
    assert store.managed_role_ids(111) == set()
    # No phantom guild/factory should have been created by the reads above.
    assert store.guilds == {}


def test_save_is_atomic_and_leaves_no_tmp(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    store = StateStore.load(path)
    store.set_role_id(111, "milk_factory", "cheese", 333)
    store.save()

    assert not path.with_suffix(path.suffix + ".tmp").exists()


def test_corrupt_file_raises(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.write_text("{ not json", encoding="utf-8")
    with pytest.raises(StateError, match="Could not read"):
        StateStore.load(path)


def test_non_object_root_raises(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.write_text("[]", encoding="utf-8")
    with pytest.raises(StateError, match="must be a JSON object"):
        StateStore.load(path)


def _reaction_store(tmp_path: Path) -> StateStore:
    store = StateStore.load(tmp_path / "state.json")
    store.set_message_id(111, "milk_factory", 222)
    store.set_role_id(111, "milk_factory", "cheese", 333)
    store.set_emoji_id(111, "milk_factory", "cheese", 444)
    return store


def test_role_id_for_reaction_maps_message_and_emoji(tmp_path: Path) -> None:
    store = _reaction_store(tmp_path)
    assert store.role_id_for_reaction(111, 222, 444) == 333


def test_role_id_for_reaction_wrong_emoji_on_right_message(tmp_path: Path) -> None:
    store = _reaction_store(tmp_path)
    # Right message, but an emoji not tied to any item on it.
    assert store.role_id_for_reaction(111, 222, 999) is None


def test_role_id_for_reaction_wrong_message(tmp_path: Path) -> None:
    store = _reaction_store(tmp_path)
    assert store.role_id_for_reaction(111, 555, 444) is None


def test_role_id_for_reaction_unicode_emoji_never_matches(tmp_path: Path) -> None:
    store = _reaction_store(tmp_path)
    # A unicode reaction has no emoji id; it must never map to a managed role.
    assert store.role_id_for_reaction(111, 222, None) is None


def test_role_id_for_reaction_unknown_guild(tmp_path: Path) -> None:
    store = _reaction_store(tmp_path)
    assert store.role_id_for_reaction(222, 222, 444) is None
