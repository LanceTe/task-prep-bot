"""Idempotently upload files from assets/emojis/ as application-owned Discord emojis.

Filename (without extension) becomes the emoji name. Re-runs are safe: existing
names are skipped so nothing is duplicated.

Usage:
    uv run python scripts/seed_emojis.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import discord

from leaf_valley.settings import BOT_TOKEN, EMOJIS_DIR

MAX_EMOJI_BYTES = 256 * 1024
ALLOWED_SUFFIXES = {".png", ".gif", ".jpg", ".jpeg", ".webp"}


def _discover_files(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    return sorted(
        p for p in root.iterdir()
        if p.is_file() and p.suffix.lower() in ALLOWED_SUFFIXES
    )


async def _seed(client: discord.Client) -> int:
    files = _discover_files(EMOJIS_DIR)
    if not files:
        print(f"No emoji files found in {EMOJIS_DIR}. Nothing to do.")
        return 0

    existing = {e.name for e in await client.fetch_application_emojis()}
    print(f"Application already has {len(existing)} emoji(s). Found {len(files)} local file(s).\n")

    uploaded = skipped = failed = 0
    for path in files:
        name = path.stem
        if name in existing:
            print(f"  skip     :{name}: (already exists)")
            skipped += 1
            continue

        size = path.stat().st_size
        if size > MAX_EMOJI_BYTES:
            print(f"  FAIL     :{name}: file is {size} bytes, max is {MAX_EMOJI_BYTES}")
            failed += 1
            continue

        try:
            emoji = await client.create_application_emoji(name=name, image=path.read_bytes())
        except discord.HTTPException as exc:
            print(f"  FAIL     :{name}: {exc}")
            failed += 1
            continue

        print(f"  uploaded :{name}: (id={emoji.id})")
        uploaded += 1

    print(f"\nSummary: {uploaded} uploaded, {skipped} skipped, {failed} failed")
    return 1 if failed else 0


class _SeedClient(discord.Client):
    exit_code: int = 0

    async def on_ready(self) -> None:
        try:
            self.exit_code = await _seed(self)
        finally:
            await self.close()


async def _main() -> int:
    client = _SeedClient(intents=discord.Intents.none())
    try:
        await client.start(BOT_TOKEN)
    except discord.LoginFailure as exc:
        print(f"Login failed: {exc}", file=sys.stderr)
        return 2
    finally:
        if not client.is_closed():
            await client.close()
    return client.exit_code


def main() -> int:
    return asyncio.run(_main())


if __name__ == "__main__":
    raise SystemExit(main())
