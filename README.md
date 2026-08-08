# Disbox

Encrypted, deduplicated file storage on Discord, with a local-first vault.

Files are split into chunks, encrypted, and stored as Discord attachments. A
local SQLite vault holds the index that maps your files to those chunks.

> **The vault file is not a cache.** It is the only record of where your data
> lives. Lose it and the chunks remain on Discord, encrypted, with nothing to
> say which file they belong to or what order they go in. Back it up.

---

## Status

Pre-alpha. The storage engine, filesystem, CLI and desktop client work and are
covered by 548 tests, but nothing here has been through a security review or run
against a large real-world corpus. Do not use it as your only copy of anything
you care about.

## Requirements

- **Python 3.14** or newer
- **[uv](https://docs.astral.sh/uv/)** for dependency management
- A Discord bot token and a channel the bot can post in

## Install

```bash
git clone https://github.com/rayanbayat/disbox
cd disbox
uv sync
```

## Configure

Storage credentials come from the environment or a `.env` file. Copy the
example and fill it in:

```bash
cp .env.example .env
```

```ini
DISBOX_BOT_TOKEN=your-bot-token
DISBOX_CHANNEL_ID=123456789012345678
```

`.env` is gitignored. The token is held as a `SecretStr` so it is not rendered
by accident in logs or tracebacks, and the settings dialog never displays it
back — it reports only whether one is configured.

Without these, Disbox still runs and stores blobs on the local disk beside the
vault instead of on Discord. Everything else behaves the same, which is how the
test suite exercises transfers without credentials.

### Getting a token

1. Create an application at <https://discord.com/developers/applications>
2. Add a **Bot** to it and copy the token
3. Invite the bot to a server with permission to read and send messages
4. Enable Developer Mode in Discord, right-click the target **channel**, and
   copy its ID — a channel ID, not a server ID

## Use

### Desktop

```bash
uv run disbox-gui
```

The picker offers recently opened vaults, or creates a new one. Creating a vault
asks for a passphrase; it wraps the master key, and there is no recovery path if
you forget it.

Once open: drag files or folders in from Explorer, or use the toolbar. Right-click
for rename, delete and properties. `Ctrl+Z` undoes the last change, `Del` moves to
the trash, `F2` renames, `Ctrl+Shift+N` makes a folder.

### Command line

```bash
uv run disbox --help
```

| Command | What it does |
|---|---|
| `ls` | List a directory |
| `tree` | Show the tree |
| `find` | Search by name |
| `put` | Upload a file or folder |
| `get` | Download a file or folder |
| `mkdir` | Create a directory |
| `mv` | Rename or move |
| `rm` | Move to the trash |

## How it works

1. **Chunking** — files are split by content, not at fixed offsets, so inserting
   a byte near the start does not change every chunk after it.
2. **Convergent encryption** — a chunk's key is derived from its own plaintext
   hash, so identical chunks encrypt identically and are stored once.
3. **Upload** — each chunk becomes a Discord attachment. The vault records the
   message and attachment ids, never the CDN URLs, which expire.
4. **Download** — chunks are fetched, decrypted, verified against their hashes,
   and reassembled.

### What this costs you

Convergent encryption is what makes deduplication possible, and it has a real
trade-off: someone holding the master key can confirm whether a **specific file
they already have** is stored in the vault, because its chunks would encrypt to
the same ciphertext. It does not reveal the contents of anything they do not
already possess. If that matters more than deduplication does, the design is
reversible to per-file keys.

## Security

- **AES-256-GCM** for chunk contents
- **Argon2id** to derive the vault key from your passphrase
- **BLAKE3** for content addressing and integrity
- Chunk contents are encrypted before they leave the machine; Discord never sees
  plaintext, and filenames are not sent as attachment names

Not yet done: an independent security review, and the threat model in
`ANALYSIS.md` has not been re-validated against the finished implementation.

## Development

```bash
uv run pytest          # tests
uv run mypy src        # types
uv run ruff check .    # lint
uv run ruff format .   # format
```

Live tests that talk to Discord are excluded by default and run with
`uv run pytest -m live`.

Working notes live in `PROGRESS.md`; the plan is `SPEC.md`; the analysis of the
original web client that prompted the rewrite is `ANALYSIS.md`.

## Licence

AGPL-3.0-or-later. See `LICENSE`.
