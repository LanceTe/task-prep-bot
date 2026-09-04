from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from leaf_valley.config.loader import ConfigError, load_factory_config

PROJECT_ROOT = Path(__file__).resolve().parents[1]
REAL_CONFIG = PROJECT_ROOT / "config" / "factories.yaml"

VALID = dedent(
    """
    factories:
      - key: dairy_processor
        name: "🥛 Dairy Processor"
        image: "dairy_processor.png"
        items:
          - key: cheese
            role_name: "cheese"
            emoji: ":cheese:"
          - key: cream
            role_name: "cream"
            emoji: ":cream:"
    """
)


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "factories.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_loads_valid_config(tmp_path: Path) -> None:
    config = load_factory_config(_write(tmp_path, VALID))

    assert len(config.factories) == 1
    factory = config.factories[0]
    assert factory.key == "dairy_processor"
    assert factory.image == "dairy_processor.png"
    assert [item.key for item in factory.items] == ["cheese", "cream"]
    assert factory.items[0].emoji_name == "cheese"


def test_null_image_is_none(tmp_path: Path) -> None:
    text = VALID.replace('image: "dairy_processor.png"', "image: null")
    config = load_factory_config(_write(tmp_path, text))
    assert config.factories[0].image is None


def test_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="not found"):
        load_factory_config(tmp_path / "nope.yaml")


def test_missing_factories_key_raises(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="factories"):
        load_factory_config(_write(tmp_path, "something: else\n"))


def test_missing_required_field_raises(tmp_path: Path) -> None:
    text = dedent(
        """
        factories:
          - key: dairy_processor
            image: null
            items:
              - key: cheese
                role_name: "cheese"
                emoji: ":cheese:"
        """
    )
    with pytest.raises(ConfigError, match="'name' is required"):
        load_factory_config(_write(tmp_path, text))


def test_duplicate_factory_key_raises(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="Duplicate factory key"):
        load_factory_config(_write(tmp_path, VALID + VALID.split("factories:")[1]))


def test_duplicate_item_key_raises(tmp_path: Path) -> None:
    text = VALID.replace(
        'role_name: "cream"\n            emoji: ":cream:"',
        'role_name: "cheese"\n            emoji: ":cheese:"',
    ).replace("- key: cream", "- key: cheese")
    with pytest.raises(ConfigError, match="duplicate item key"):
        load_factory_config(_write(tmp_path, text))


def test_too_many_items_raises(tmp_path: Path) -> None:
    items = "\n".join(
        f'          - key: item{i}\n            role_name: "item{i}"\n            emoji: ":item{i}:"'
        for i in range(21)
    )
    text = (
        dedent(
            """
        factories:
          - key: big
            name: "Big"
            image: null
            items:
        """
        )
        + items
        + "\n"
    )
    with pytest.raises(ConfigError, match="exceeding the Discord"):
        load_factory_config(_write(tmp_path, text))


def test_bad_emoji_format_raises(tmp_path: Path) -> None:
    text = VALID.replace('emoji: ":cheese:"', 'emoji: "cheese"')
    with pytest.raises(ConfigError, match="application-emoji reference"):
        load_factory_config(_write(tmp_path, text))


def test_real_config_loads() -> None:
    config = load_factory_config(REAL_CONFIG)
    assert config.factories
    # Every factory has at least one item and no factory exceeds the reaction cap.
    for factory in config.factories:
        assert factory.items
        assert len(factory.items) <= 20
