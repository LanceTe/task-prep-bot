# Leaf Valley

A Discord bot for a farming-game community that runs **reaction-role signups**. Each
**factory** gets its own embed message, and every **item** that factory produces is a
reaction (emoji) under it. Reacting grants the matching role so a task lead can `@ping`
everyone who signed up to prep a given item. A second, independent board lets members
pick their **name colour**.

See [docs/PLAN.md](docs/PLAN.md) for the full design rationale.

---

## Features

### Item reaction roles (rally signups)
- **One embed message per factory**, posted from `config/factories.yaml`, each with an
  optional local image attachment from `assets/factories/` and a legend mapping each
  reaction to its item.
- **Add role on reaction, remove role on un-reaction** via raw reaction events, so
  mappings survive bot restarts (no message cache needed).
- **Additive selection** - a member can sign up for many items at once.
- **Mentionable item roles** so a lead can `@ping` everyone prepping a given item.
- **Custom application-owned emojis** as reactions, resolved by name at setup time.

### Name-colour reaction roles
- A **single colour-picker board** posted from `config/colours.yaml`.
- **Exclusive selection** - reacting a new colour grants it and automatically removes any
  previously chosen colour role and its reaction, so a member shows exactly one colour.
- **Non-mentionable** coloured roles; the highest coloured role sets the member's name
  colour (Discord behaviour).
- Uses **unicode emojis** (🔴🟠🟡🟢🔵🟣) - no emoji seeding required.
- **Untouched by the weekly reset**, since a name colour is a personal preference, not a
  rally signup.

### Admin slash commands
All commands are guild-only and restricted to the guild owner or members holding the
admin role (`LT` by default, configurable via `ADMIN_ROLE_NAME`).

| Command | Purpose |
|---------|---------|
| `/create-roles` | Create any missing item roles (mentionable) and link them in state. Idempotent - adopts existing roles by name. |
| `/setup-factories` | Post/refresh each factory message in the current channel and seed its reactions. Keeps a single board per server. |
| `/create-colours` | Create any missing name-colour roles (coloured, non-mentionable) and link them in state. Idempotent. |
| `/setup-colours` | Post/refresh the single colour-picker board in the current channel and seed its reactions. |
| `/reset` | Remove every item role from all members and wipe reaction signups, then re-seed the board for a fresh week. Confirmation-guarded. |
| `/teardown` | Delete all factory messages and their reactions (item roles are kept). Confirmation-guarded; use at the end of a rally. |

### Safety & reliability
- **Idempotent setup** - re-running create/setup commands adopts existing roles and
  refreshes messages instead of duplicating them.
- **Confirmation prompts** on the destructive `/reset` and `/teardown` commands, scoped
  to the admin who triggered them and time-limited.
- **Single board per server** - setup refuses to post a second copy and points you at the
  existing channel.
- **Clear, actionable errors** for missing permissions (Manage Roles, Manage Messages,
  Add Reactions), role-hierarchy problems, and un-seeded custom emojis.
- **Persistent state** in `data/state.json` maps message + emoji → role, so signups
  survive restarts.

### Emoji seed script
- `scripts/seed_emojis.py` uploads every image in `assets/emojis/` as an
  **application-owned emoji** (filename without extension becomes the emoji name).
- **Idempotent by name** - it fetches existing application emojis first and only uploads
  missing ones, so re-runs never create duplicates.
- Application emojis cap at 2000 (vs. 50 per guild), need no "Manage Emojis" permission,
  and work in any guild the bot joins.

---

## Tech stack

- **Python 3.13**, managed with [`uv`](https://docs.astral.sh/uv/).
- [`discord.py`](https://discordpy.readthedocs.io/) ≥ 2.7 (application-emoji APIs).
- `PyYAML` for config, `python-dotenv` for secrets.
- `ruff` (lint/format) and `pytest` (tests) for development.

---

## Setup

### 1. Install dependencies
```bash
uv sync
```

### 2. Configure secrets
```bash
cp .env.example .env
```
Fill in `.env`:
- `BOT_TOKEN` - from the Discord Developer Portal → your app → Bot.
- `ADMIN_ROLE_NAME` - optional, defaults to `LT`.
- `GUILD_ID` - optional; when set, slash commands sync to that guild instantly instead
  of globally (global syncs can take up to an hour to propagate).

### 3. Discord Developer Portal
- Enable the **Server Members Intent**.
- Invite with scopes `bot` + `applications.commands` and permissions: **Manage Roles**,
  **View Channel**, **Send Messages**, **Add Reactions**, **Read Message History**,
  **Manage Messages** (for `/reset` and `/teardown`).
- Move the bot's role **above** every item and colour role, or it can't assign them.

### 4. Define your content
- Edit `config/factories.yaml` - factories, items, emoji names and role names.
- Edit `config/colours.yaml` - colours, unicode emojis and hex values.
- Drop custom emoji images into `assets/emojis/` and factory artwork into
  `assets/factories/`.

---

## Usage

Seed custom emojis, then run the bot:
```bash
make seed-emojis      # uploads assets/emojis/ as application emojis (idempotent)
make run              # start the bot
```

Then, in Discord, run the setup commands (first-run order):

1. `/create-roles` → `/setup-factories`  (item signups)
2. `/create-colours` → `/setup-colours`  (name colours)

Each week: `/reset` clears signups and re-seeds the boards. At the end of a rally,
`/teardown` removes the factory messages entirely.

---

## Development

```bash
make format    # auto-fix lint issues and format with ruff
make lint      # lint and format-check
make test      # run the pytest suite
make help      # list all targets
```

---

## Deployment

A systemd unit is provided in [deploy/leaf-valley.service](deploy/leaf-valley.service).
It runs the venv Python directly, restarts on failure, and confines writes to the
`data/` directory.

```bash
sudo cp deploy/leaf-valley.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now leaf-valley
journalctl -u leaf-valley -f    # follow logs
```

---

## Project layout

```
config/          factories.yaml + colours.yaml (source of truth)
assets/emojis/   custom emoji images (seeded to the application)
assets/factories/ factory embed artwork
data/state.json  runtime IDs written by the bot (git-ignored)
scripts/         seed_emojis.py - idempotent emoji uploader
src/leaf_valley/
  bot.py         intents, shared config/state, cog loading, command sync
  settings.py    env vars + project paths
  cogs/          Discord-facing commands and reaction listeners
  services/      business logic (roles, factories, colours)
  storage/       state.json read/write
  config/        YAML loader + schema
tests/           pytest suite
deploy/          systemd unit
docs/PLAN.md     full design document
```
