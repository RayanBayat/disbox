Encrypted, deduplicated file storage on Discord, with a local-first vault.

## ⚠️ Pre-alpha — read this first

**Do not use Disbox as the only copy of anything you care about.** It has not
had an independent security review, and it has not been run against a large
real-world corpus.

**Your vault file is not a cache.** It is the only record of where your data
lives. Lose it and the chunks stay on Discord, encrypted, with nothing to say
which file they belong to or what order they go in. Back it up.

## Install

Download `disbox-windows.zip`, extract it anywhere, and run:

- **`Disbox.exe`** — the desktop application
- **`disbox-cli.exe`** — the command line (`disbox-cli --help`)

If a `disbox-*-setup.exe` is attached, that installer does the same thing and
adds a Start-menu entry and `.dbx` file association.

Windows SmartScreen will warn on first run: the binaries are **not code-signed**.
Verify what you downloaded against `SHA256SUMS.txt` if you want to be sure it is
the file this workflow built.

## Setting up Discord storage

Without a bot token, Disbox stores blobs on your local disk beside the vault and
everything else works the same. To use Discord:

1. Create an application at <https://discord.com/developers/applications>
2. Add a **Bot** and copy its token
3. Invite the bot to a server where it can read and send messages
4. Enable Developer Mode, right-click the target **channel**, copy its ID —
   a channel ID, not a server ID
5. Put both in Settings, or in a `.env` beside the vault

## What works

Browsing, search, create, rename, delete, undo, a recoverable trash, folder
upload and download, drag in from Explorer and drag out to it, and transfers to
a real Discord channel — verified end to end, byte-identical on the way back.

## Known limitations

- **Uploads run at roughly 5 MiB/s** and are bound by the content-defined
  chunker, not by encryption or the network. Downloads are 60–90× faster. A
  large backup will take a while. See `docs/benchmarks.md`.
- **The security review was done by the author**, not independently. It found
  and fixed a path-traversal issue; see `docs/security-review.md`.
- **Text can appear smeared** in the window after navigating. Cosmetic.
- **Drag out and folder download pause** while data is fetched rather than
  streaming with progress.
- **Nothing is code-signed.**

## Licence

AGPL-3.0-or-later.
