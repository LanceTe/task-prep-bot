from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from leaf_valley.config.loader import ConfigError, load_colour_config

PROJECT_ROOT = Path(__file__).resolve().parents[1]
REAL_CONFIG = PROJECT_ROOT / "config" / "colours.yaml"

VALID = dedent(
    """
    colours:
      - key: red
        role_name: "🎨 Red"
        emoji: "🔴"
        colour: "#e74c3c"
      - key: blue
        role_name: "🎨 Blue"
        emoji: "🔵"
        colour: "#3498db"
    """
)


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "colours.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_loads_valid_config(tmp_path: Path) -> None:
    config = load_colour_config(_write(tmp_path, VALID))

    assert [c.key for c in config.colours] == ["red", "blue"]
    assert config.colours[0].role_name == "🎨 Red"
    assert config.colours[0].emoji == "🔴"
    assert config.colours[0].colour == 0xE74C3C


def test_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="not found"):
        load_colour_config(tmp_path / "nope.yaml")


def test_missing_colours_key_raises(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="colours"):
        load_colour_config(_write(tmp_path, "something: else\n"))


def test_empty_colours_raises(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="non-empty list"):
        load_colour_config(_write(tmp_path, "colours: []\n"))


def test_missing_required_field_raises(tmp_path: Path) -> None:
    text = dedent(
        """
        colours:
          - key: red
            emoji: "🔴"
            colour: "#e74c3c"
        """
    )
    with pytest.raises(ConfigError, match="'role_name' is required"):
        load_colour_config(_write(tmp_path, text))


def test_application_emoji_reference_rejected(tmp_path: Path) -> None:
    text = VALID.replace('emoji: "🔴"', 'emoji: ":red:"')
    with pytest.raises(ConfigError, match="unicode emoji"):
        load_colour_config(_write(tmp_path, text))


def test_bad_hex_colour_raises(tmp_path: Path) -> None:
    text = VALID.replace('colour: "#e74c3c"', 'colour: "#nothex"')
    with pytest.raises(ConfigError, match="RRGGBB"):
        load_colour_config(_write(tmp_path, text))


def test_short_hex_colour_raises(tmp_path: Path) -> None:
    text = VALID.replace('colour: "#e74c3c"', 'colour: "#fff"')
    with pytest.raises(ConfigError, match="RRGGBB"):
        load_colour_config(_write(tmp_path, text))


def test_duplicate_key_raises(tmp_path: Path) -> None:
    text = VALID.replace("- key: blue", "- key: red")
    with pytest.raises(ConfigError, match="Duplicate colour key"):
        load_colour_config(_write(tmp_path, text))


def test_duplicate_emoji_raises(tmp_path: Path) -> None:
    text = VALID.replace('emoji: "🔵"', 'emoji: "🔴"')
    with pytest.raises(ConfigError, match="Duplicate colour emoji"):
        load_colour_config(_write(tmp_path, text))


def test_too_many_colours_raises(tmp_path: Path) -> None:
    entries = "\n".join(
        f'      - key: c{i}\n        role_name: "c{i}"\n'
        f'        emoji: "{chr(0x1F300 + i)}"\n        colour: "#010203"'
        for i in range(21)
    )
    text = "colours:\n" + entries + "\n"
    with pytest.raises(ConfigError, match="exceeding the Discord"):
        load_colour_config(_write(tmp_path, text))


def test_real_config_loads() -> None:
    config = load_colour_config(REAL_CONFIG)
    assert config.colours
    assert len(config.colours) <= 20
    # Emojis are distinct (validated) and colours parsed to ints.
    assert len({c.emoji for c in config.colours}) == len(config.colours)
    assert all(isinstance(c.colour, int) for c in config.colours)
