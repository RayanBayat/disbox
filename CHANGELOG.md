# Changelog

Notable changes, newest first. Follows [Keep a Changelog](https://keepachangelog.com/)
loosely and [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.1.0] — 2026-08-08

First release. Pre-alpha: usable, not yet trustworthy as a sole copy.

### Added

- **Vault.** SQLite index with WAL, migrations, integrity checks, snapshots and
  a single-writer lock. Refuses to open a damaged file rather than migrating it.
- **Crypto.** AES-256-GCM chunk contents, Argon2id passphrase derivation,
  BLAKE3 content addressing, convergent encryption for deduplication.
- **Chunking.** Content-defined boundaries, so inserting a byte near the start
  does not change every chunk after it.
- **Discord backend.** Chunks stored as attachments; the vault records message
  and attachment ids rather than CDN URLs, which expire.
- **Transfer engine.** Resumable uploads and downloads, bounded concurrency,
  session checkpointing, per-chunk integrity verification.
- **Filesystem.** Directories, rename, move, soft delete with a recoverable
  trash, collision policies, FTS5 search.
- **Undo** over the journal, covering create, rename, delete, restore and move.
- **Desktop client.** Qt 6, Mica backdrop, light and dark themes, folder tree,
  transfer dock, properties, settings, notification centre with copyable
  diagnostic identifiers, and no blocking modal on any error path.
- **Drag and drop** in from Explorer and out to it, deferred until the drop.
- **Command line.** `ls`, `tree`, `find`, `put`, `get`, `mkdir`, `mv`, `rm`,
  plus vault and trash subcommands.
- **Packaging.** PyInstaller bundle carrying both programs, Inno Setup script,
  and a tag-triggered release workflow that round-trips a vault through the
  packaged binary before publishing.

### Fixed during development

- **Path traversal through node names.** Backslash was not rejected, so a name
  like `..\..\evil` could escape a download directory on Windows. Colons,
  reserved device names and trailing dots are now refused too.
- **The desktop client never built a transfer engine**, so every upload
  reported that storage was unconfigured. Found while writing the README.
- **Download and upload had no way to be invoked** from the interface — the
  methods existed and were tested, but no button or menu entry reached them.
- **Schema migrations were not shipped in the bundle**, so the packaged build
  failed on the first vault it opened.

### Known limitations

- Uploads run at roughly 5 MiB/s, bound by the chunker rather than the network.
- The security review was performed by the author, not independently.
- Binaries are not code-signed.
- Text can appear smeared after navigating in the window.

[Unreleased]: https://github.com/RayanBayat/disbox/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/RayanBayat/disbox/releases/tag/v0.1.0
