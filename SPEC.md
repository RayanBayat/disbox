# Disbox v2 — Specification & Work Breakdown

**Status:** Draft 1 · **Date:** 2026-08-07
**Supersedes:** the React/CRA web client analyzed in [`ANALYSIS.md`](./ANALYSIS.md)
**Type:** Full rewrite. No code is carried over; `disbox-file-manager.js` is treated as reference documentation for the Discord protocol only.

---

## 0. Locked decisions

| # | Decision | Choice | Consequence |
|---|---|---|---|
| D1 | Language / runtime | **Python 3.14**, managed by **uv** | Free-threading available, `compression.zstd` in stdlib, `uuid7()`, `concurrent.interpreters` |
| D2 | Delivery form | **Native desktop application.** Not a website, not a webview, no browser involved | No CORS problem, no browser extension, no third-party proxy, no `localStorage` |
| D3 | UI toolkit | **PySide6 / Qt 6** | Virtualized `QAbstractItemModel` over SQLite; native drag-and-drop, tray, file dialogs |
| D4 | Metadata store | **A single local SQLite file — the *vault*** | No central server. The vault file is the product's most valuable artifact |
| D5 | Discord credential | **Bot token** | Per-route rate buckets, message pagination, `attachments/refresh-urls`, channel rescan |
| D6 | Encryption | **AES-256-GCM, on by default** | Discord stores ciphertext only; Argon2id-derived master key |
| D7 | OS drive mount | **Deferred to v2**, engine kept mount-ready | Read path must support ranged, out-of-order chunk reads from day one |
| D8 | Storage abstraction | `StorageBackend` Protocol; Discord + Local implementations in v1 | ~80 lines of insurance against Discord ToS enforcement (see `ANALYSIS.md` §9.1) |

### Assumptions being made (flag if wrong)

- **A1** — Single-user, single-machine primary use. Multi-device is served by copying/syncing the vault file, not by a server. Concurrent multi-writer is explicitly out of scope for v1.
- **A2** — Target OS priority: **Windows 11 first** (your platform), then Linux, then macOS. Code stays cross-platform; only Windows is CI-verified and packaged in v1.
- **A3** — The Discord attachment size limit is **discovered at runtime**, never hardcoded. See §5.4.
- **A4** — You accept the Discord ToS risk (`ANALYSIS.md` §9.1). The app will state it once at setup and then stop nagging.

---

## 1. Goals and non-goals

### Goals

- **G1 — Total filesystem control.** Every operation a real file manager has: create, rename, move, copy, recursive delete, folder upload, folder download, multi-select bulk ops, trash with restore, cut/copy/paste, drag-and-drop both directions, versions, search, properties.
- **G2 — The vault is authoritative and self-healing.** It is a local file, it is precious, and it is *also* backed up into Discord and fully reconstructible from Discord if lost.
- **G3 — Performance is a feature.** Parallel transfers, content-defined chunking with dedup, zero full-file buffering, a UI that stays at 60 fps with a quarter-million rows.
- **G4 — Correct by construction.** Every failure mode from `ANALYSIS.md` §5–§7 is either structurally impossible or covered by a test.
- **G5 — No trust in third parties.** No metadata server, no proxy, no browser extension, no plaintext on Discord.

### Non-goals for v1

- Multi-user sharing, public share links, or any hosted service component.
- Real-time sync between machines (the vault is copied manually or via your own Dropbox/Syncthing).
- Mobile clients.
- OS drive mounting (D7).
- Erasure coding across multiple providers (design-compatible, built in v2).

---

## 2. Architecture

```
┌──────────────────────────────────────────────────────────────┐
│  disbox.gui           PySide6 / Qt 6                          │
│  MainWindow · TreeView · FileTableView (virtualized)          │
│  TransferDock · TrashView · SearchBar · Properties            │
│         │ Qt signals/slots            ▲ progress events       │
│         ▼                              │                      │
│  disbox.gui.bridge   QThread ⇄ asyncio event loop bridge      │
└─────────┬────────────────────────────────────────────────────┘
          │  async API (also consumed directly by the CLI)
┌─────────▼────────────────────────────────────────────────────┐
│  disbox.core                                                  │
│                                                               │
│  FileSystem      tree ops, paths, trash, versions, search     │
│  TransferEngine  parallel up/down, resume, cancel, verify     │
│  Chunker         FastCDC → content-defined boundaries         │
│  Crypto          Argon2id · HKDF · AES-256-GCM · BLAKE3       │
│  Vault           SQLite (WAL), migrations, snapshots, GC      │
│  RateLimiter     Discord bucket-aware token buckets           │
└─────────┬────────────────────────────────────────────────────┘
          │  StorageBackend Protocol
   ┌──────┴───────┬─────────────────┐
   ▼              ▼                 ▼
DiscordBackend  LocalBackend   (v2: S3Backend, B2Backend)
bot token       filesystem
httpx/HTTP2     used by tests
```

**Threading model.** One asyncio event loop on a dedicated `QThread` owns all I/O. CPU work (AES-GCM, BLAKE3, zstd) runs in a `ThreadPoolExecutor` — the underlying C extensions release the GIL, so this is *genuinely* parallel on stock CPython 3.14. The free-threaded build is an optimization to evaluate (task `M0-2`), not a dependency. The Qt main thread never blocks; it receives progress via queued signals.

---

## 3. The Vault — local metadata database

This is the heart of the design and your explicit requirement. Spec it carefully.

### 3.1 Identity and location

- One file: `vault.dbx` — a SQLite 3 database.
- Default location: `%LOCALAPPDATA%\Disbox\vaults\<vault-name>\vault.dbx` (Windows), XDG equivalents elsewhere.
- The user may relocate it, and may keep multiple vaults (different Discord channels / different purposes). A vault picker appears at launch when more than one is registered.
- Every vault carries an immutable `vault_id` (UUIDv7) generated at creation and stamped into every chunk header — this is what makes rescan-rebuild possible.

### 3.2 Durability requirements

| ID | Requirement |
|---|---|
| V1 | `journal_mode=WAL`, `synchronous=FULL`, `foreign_keys=ON`, `busy_timeout=5000`. |
| V2 | Every logical operation is a single transaction. Partial states must never be observable. |
| V3 | **Snapshot rotation.** Before every app start, and after every *N*=200 mutations, take a snapshot via SQLite's Online Backup API into `snapshots/vault-<utc-iso>.dbx`. Keep the last 15 plus one per calendar day for 30 days. Snapshots are taken while the app runs — no locking stall. |
| V4 | **Append-only journal table.** Every mutation writes `(id, ts, op, target_id, payload_json, actor)`. Never pruned automatically; exportable. This is the forensic record when something goes wrong. |
| V5 | **Startup integrity check.** `PRAGMA quick_check` on every launch; full `PRAGMA integrity_check` weekly or on demand. On failure: refuse to write, offer restore-from-snapshot. |
| V6 | **Encrypted cloud backup.** After every N mutations or T minutes (whichever first), upload a zstd-compressed, AES-GCM-encrypted copy of the vault to the Discord channel, tagged as a vault snapshot. Retain the last 10 remotely. |
| V7 | **Full rebuild from Discord.** `disbox rebuild` re-scans the channel and reconstructs a working vault from chunk headers alone, even with zero local state. See §3.5. |
| V8 | **Export / import.** `disbox export --out vault.json` produces a human-readable, self-describing manifest (schema version, all nodes, all chunk refs). `import` reverses it. This is the escape hatch that outlives the app. |
| V9 | The vault must be safely copyable while the app is closed, and must survive being placed in Dropbox/OneDrive/Syncthing (single-writer only — enforced by a lockfile with PID + hostname). |

### 3.3 Schema

```sql
PRAGMA user_version = 1;

CREATE TABLE meta (                  -- single row
  vault_id        BLOB PRIMARY KEY,  -- UUIDv7
  schema_version  INTEGER NOT NULL,
  created_at      TEXT NOT NULL,
  kdf_salt        BLOB NOT NULL,     -- Argon2id salt
  kdf_params      TEXT NOT NULL,     -- json: {t, m, p}
  wrapped_mk      BLOB NOT NULL,     -- AES-GCM(KEK, master_key)
  mk_check        BLOB NOT NULL      -- verifies the passphrase without decrypting data
);

CREATE TABLE backends (
  id           INTEGER PRIMARY KEY,
  kind         TEXT NOT NULL,        -- 'discord' | 'local'
  label        TEXT NOT NULL,
  config_enc   BLOB NOT NULL,        -- AES-GCM: bot token, channel id, guild id
  max_blob     INTEGER NOT NULL,     -- discovered, not hardcoded  (ANALYSIS §9.2)
  probed_at    TEXT,
  is_default   INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE nodes (
  id           BLOB PRIMARY KEY,     -- UUIDv7: time-ordered, good index locality
  parent_id    BLOB REFERENCES nodes(id) ON DELETE RESTRICT,
  name         TEXT NOT NULL,        -- plaintext locally; see §3.4 threat model
  kind         TEXT NOT NULL,        -- 'dir' | 'file'
  size         INTEGER NOT NULL DEFAULT 0,
  created_at   TEXT NOT NULL,
  modified_at  TEXT NOT NULL,
  deleted_at   TEXT,                 -- soft delete → trash
  version      INTEGER NOT NULL DEFAULT 1,   -- optimistic concurrency
  current_rev  INTEGER REFERENCES revisions(id),
  mime         TEXT,
  UNIQUE (parent_id, name, deleted_at)
);
CREATE INDEX idx_nodes_parent  ON nodes(parent_id) WHERE deleted_at IS NULL;
CREATE INDEX idx_nodes_trash   ON nodes(deleted_at) WHERE deleted_at IS NOT NULL;

CREATE TABLE revisions (             -- file history; cheap because chunks are shared
  id           INTEGER PRIMARY KEY,
  node_id      BLOB NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
  created_at   TEXT NOT NULL,
  size         INTEGER NOT NULL,
  merkle_root  BLOB NOT NULL,
  chunk_count  INTEGER NOT NULL
);

CREATE TABLE chunks (
  hash         BLOB PRIMARY KEY,     -- BLAKE3 of PLAINTEXT chunk (dedup key)
  size         INTEGER NOT NULL,     -- plaintext size
  stored_size  INTEGER NOT NULL,     -- after zstd + AES-GCM
  backend_id   INTEGER NOT NULL REFERENCES backends(id),
  message_id   TEXT NOT NULL,        -- Discord message snowflake
  attach_id    TEXT NOT NULL,
  refcount     INTEGER NOT NULL DEFAULT 0,
  verified_at  TEXT,
  UNIQUE (backend_id, message_id, attach_id)
);
CREATE INDEX idx_chunks_gc ON chunks(refcount) WHERE refcount = 0;

CREATE TABLE revision_chunks (
  revision_id  INTEGER NOT NULL REFERENCES revisions(id) ON DELETE CASCADE,
  idx          INTEGER NOT NULL,
  chunk_hash   BLOB NOT NULL REFERENCES chunks(hash),
  PRIMARY KEY (revision_id, idx)
);

CREATE TABLE upload_sessions (       -- resume across crashes and restarts
  id           BLOB PRIMARY KEY,
  node_id      BLOB REFERENCES nodes(id) ON DELETE CASCADE,
  source_path  TEXT NOT NULL,
  total_size   INTEGER,
  state        TEXT NOT NULL,        -- 'active' | 'paused' | 'failed' | 'done'
  completed    TEXT NOT NULL,        -- json: {chunk_idx: chunk_hash}
  created_at   TEXT NOT NULL,
  updated_at   TEXT NOT NULL
);

CREATE TABLE journal (               -- V4, append-only
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  ts           TEXT NOT NULL,
  op           TEXT NOT NULL,
  target_id    BLOB,
  payload      TEXT
);

CREATE TABLE vault_backups (         -- V6, remote copies of this very file
  id           INTEGER PRIMARY KEY,
  created_at   TEXT NOT NULL,
  message_id   TEXT NOT NULL,
  size         INTEGER NOT NULL,
  node_count   INTEGER NOT NULL
);

CREATE VIRTUAL TABLE nodes_fts USING fts5(
  name, content='nodes', content_rowid='rowid', tokenize='trigram'
);
```

**Design notes tied to the old bugs:**

- Paths are **derived** from `parent_id`, never stored as strings — the `renameFile` substring corruption (`ANALYSIS.md` §5.4) cannot occur.
- `version` gives optimistic concurrency, replacing last-writer-wins (§7.7).
- `refcount` drives safe garbage collection, replacing "orphans forever" (§7.2).
- `deleted_at` gives a real trash bin; delete is recoverable.
- `max_blob` is a per-backend, runtime-probed column — not a constant (§9.2).
- FTS5 trigram index makes substring search instant; no client-side tree walk (§5.7, §5.8).

### 3.4 Encryption and threat model

```
passphrase ──Argon2id(salt, t=3, m=256MiB, p=4)──► KEK
                                                     │
                       wrapped_mk ──AES-GCM-unwrap───┴──► master_key (32B, memory only)
                                                             │
node/revision ──HKDF-SHA256(master_key, info=node_id)──► file_key
chunk i ──nonce = HKDF(file_key, info=b"nonce"||idx)[:12]──► AES-256-GCM(chunk)
```

- The **master key is never written unwrapped.** It lives in locked memory for the session; the app zeroizes on exit and re-prompts after a configurable idle lock (default 30 min).
- Optional: wrap the KEK with the **OS keyring** (Windows DPAPI / Credential Manager via `keyring`) so a trusted machine doesn't prompt every launch. Explicitly opt-in.
- The **bot token and channel ID are encrypted** in `backends.config_enc` — stealing the vault file yields no Discord access.
- **Filenames are plaintext in the local DB.** Rationale: this file lives on your own machine behind OS permissions, and plaintext names are what make FTS5 search instant. What crosses the network is always ciphertext. If you want the vault itself opaque at rest, `M7-8` adds an optional SQLCipher whole-file mode as a toggle — deliberately scheduled after v1 so it can't delay the core.
- **What Discord sees:** opaque encrypted blobs of near-uniform size, no filenames, no structure, no MIME types.

### 3.5 Chunk header — what makes rebuild possible

Every uploaded blob carries a self-describing encrypted header, so the channel alone is a complete backup:

```
┌────────┬─────────┬──────────┬───────────────────────┬──────────────┐
│ "DBX2" │ ver u8  │ hlen u16 │ AES-GCM(header_cbor)  │  payload…    │
│  4 B   │  1 B    │   2 B    │      hlen bytes       │  ciphertext  │
└────────┴─────────┴──────────┴───────────────────────┴──────────────┘

header_cbor = {
  vault_id, node_id, revision_id, chunk_idx, chunk_count,
  plaintext_hash, plaintext_size, name_hint, path_hint, created_at
}
```

`disbox rebuild` then: paginates the channel with the bot token → downloads headers only (ranged reads, first 4 KB) → decrypts with the master key → reconstructs `nodes`, `revisions`, `chunks`, and `revision_chunks`. **Losing the vault file costs you a rescan, not your data.** This is the single most important resilience property in the design, and it is only available because of decision D5.

### 3.6 Deletion and garbage collection

Ordered so that a crash at any point is recoverable — the exact inverse of the old client's delete (`ANALYSIS.md` §7.2):

```
1. Soft delete:  nodes.deleted_at = now                       (instant, undoable)
2. Purge (user-initiated or trash retention expiry):
   2a. TX: decrement refcount on every chunk of every revision
   2b. TX: delete revision_chunks, revisions, node rows
   2c. COMMIT                      ← vault is now consistent
   2d. Background: for chunks where refcount = 0 and age > grace(24h),
       DELETE the Discord message, then DELETE the chunk row
   2e. Periodic VACUUM
```

Step 2d is idempotent and resumable: if it dies, the chunk row still exists with `refcount = 0`, and the next GC pass retries. Dangling pointers are structurally impossible because the metadata is always deleted *after* it stops being referenced and *before* the blob goes away.

`disbox verify` walks the other direction — every referenced chunk is checked for existence on Discord, and anything missing is reported with the affected file list.

---

## 4. Core engine

### 4.1 Chunking

- **FastCDC** content-defined chunking. Target average = `backend.max_blob - header_overhead - gcm_tag`, min = ½ target, max = 2× target.
- Content-defined boundaries mean an insertion near the start of a file re-uploads one chunk, not all of them — fixed-offset chunking would re-upload everything.
- Chunk hash = **BLAKE3 of plaintext** → global dedup key within the vault.
- Optional **zstd** compression (stdlib `compression.zstd`, PEP 784) before encryption, level 3, skipped when the first 64 KB show entropy above a threshold (already-compressed media).

### 4.2 Manifest & integrity

- A revision is an ordered list of chunk hashes plus a **BLAKE3 Merkle root**.
- Verify on every download: per-chunk hash, then the root. Mismatch = hard failure with a specific chunk index, never silent corruption (`ANALYSIS.md` §7.6).
- `disbox verify --deep` re-downloads and re-hashes a sampled percentage on a schedule.

### 4.3 Transfer engine

| ID | Requirement |
|---|---|
| T1 | Bounded-concurrency fan-out via `asyncio.TaskGroup` + `Semaphore`, default 8 chunks in flight, tunable 1–32. |
| T2 | **Resumable.** State checkpointed to `upload_sessions` after every chunk. Restarting the app offers to resume; only missing indices are re-sent. |
| T3 | **Idempotent.** `idempotency_key = BLAKE3(session_id ‖ chunk_idx)`; a retried chunk never creates a duplicate message. |
| T4 | **Retries** via `tenacity`: exponential backoff with jitter, `stop_after_attempt(6)`, only on transport errors and 5xx/429. Bounded — no unbounded recursion (`ANALYSIS.md` §5.12). |
| T5 | **Cancellable.** Every operation takes a cancel token; cancelling stops in-flight requests and rolls the session back to `paused`. |
| T6 | **Streaming.** Never materialize a whole file. Peak memory ≤ `concurrency × max_blob × 2`. |
| T7 | **Ranged reads.** `read(node, offset, length)` assembles only the covering chunks — required for v2 mounting (D7) and for preview/thumbnail generation. |
| T8 | **Dedup short-circuit.** A chunk whose hash already exists with `refcount ≥ 1` is never re-uploaded; only the refcount changes. |
| T9 | Per-transfer and aggregate throughput reported at ≥4 Hz for the UI, with an ETA that uses a rolling window, not a naive average. |

### 4.4 Rate limiting

- Keyed on Discord's **`X-RateLimit-Bucket`** response header, not on a caller-supplied string (`ANALYSIS.md` §7.9).
- Per-bucket `aiolimiter.AsyncLimiter` plus a global 50 req/s ceiling.
- Honor `Retry-After` and `X-RateLimit-Reset-After` with jitter; proactively pause a bucket at `Remaining == 0`.
- Shared across the whole process; the vault records observed limits so a restart doesn't start over blind.

---

## 5. Discord backend

### 5.1 Credentials & setup

Guided wizard: create application → add bot → copy token → invite to your server with a minimal scope (`bot`, permissions: View Channel, Send Messages, Attach Files, Read Message History, Manage Messages) → pick the target channel. The wizard validates each step live and stores the token encrypted.

### 5.2 API surface used

| Operation | Endpoint |
|---|---|
| Upload chunk | `POST /channels/{ch}/messages` (multipart) |
| Resolve URL | `GET /channels/{ch}/messages/{id}` |
| **Refresh expired URLs** | `POST /channels/{ch}/attachments/refresh-urls` (batch) |
| Delete chunk | `DELETE /channels/{ch}/messages/{id}` |
| Bulk delete | `POST /channels/{ch}/messages/bulk-delete` (≤100, <14 days old) |
| Rescan for rebuild | `GET /channels/{ch}/messages?before=…&limit=100` |
| Download blob | `GET {cdn_url}` with HTTP Range |

### 5.3 Signed-URL expiry

Discord CDN URLs are signed and expire (~24 h) — the defect that silently broke the old client's share links (`ANALYSIS.md` §5.1). Handling:

- **Never persist a CDN URL** as a durable reference. The vault stores `(message_id, attach_id)` only.
- Resolve URLs lazily at download time; cache in memory with a TTL derived from the `ex=` parameter.
- On a 403/404 from the CDN, batch-refresh via `attachments/refresh-urls` and retry once.

### 5.4 Attachment size negotiation (`A3`)

On backend setup and every 7 days: read the guild's `premium_tier`, derive the candidate limit, then **empirically probe** with a binary search over a small ladder of test uploads (deleted immediately). Store the result in `backends.max_blob`. If Discord changes the limit tomorrow, the next probe adapts and only *new* chunks change size — existing files are unaffected.

### 5.5 HTTP client

`httpx.AsyncClient` with HTTP/2, connection pool of 20, `Timeout(connect=10, read=300)`, and a persistent `Bot` auth header. One client per backend instance, explicitly closed on shutdown.

---

## 6. GUI specification (PySide6)

### 6.1 Windows and views

| View | Contents |
|---|---|
| **Vault picker** | Launch screen when >1 vault registered; create / open / import. |
| **Unlock** | Passphrase prompt, Argon2id progress, "remember on this machine" (OS keyring) toggle. |
| **Setup wizard** | Discord bot creation walkthrough, channel picker, size probe, encryption passphrase, ToS acknowledgement (A4). |
| **Main window** | Split view: folder tree (left) + file table (right) + transfer dock (bottom, collapsible) + status bar. |
| **Trash** | Deleted nodes with restore / purge / empty, retention setting. |
| **Properties** | Size, revisions, chunk count, dedup ratio, Merkle root, backend, verify button, per-chunk map. |
| **Settings** | Concurrency, chunk size override, compression, snapshot cadence, remote backup cadence, theme, idle lock. |

### 6.2 Performance requirements

| ID | Requirement |
|---|---|
| U1 | The file table is a **custom `QAbstractTableModel` reading paged SQLite queries** — never a full in-memory list. Target: 250 000 rows in a folder, 60 fps scroll. |
| U2 | Directory switch renders in **< 16 ms** (indexed query, `LIMIT`/`OFFSET` paging). |
| U3 | Cold start to interactive **< 400 ms**, excluding Argon2id (which shows its own progress). |
| U4 | Search results update within **< 50 ms** of a keystroke on a 250 k-node vault (FTS5 trigram + 120 ms debounce). |
| U5 | The Qt main thread must never block on I/O. Any operation exceeding 100 ms goes to the async loop with progress reporting. |
| U6 | Idle RAM < 150 MB; during 8 concurrent transfers < 400 MB. |

### 6.3 Filesystem operations (G1 — "total control")

Every one of these is a v1 requirement:

- New folder · Rename (inline edit, validated) · Move (drag within tree, or cut/paste) · Copy (dedup makes it near-instant — no re-upload)
- **Recursive folder delete** — the old client could not do this at all
- **Folder upload** (recursive, preserving structure) — old client could not
- **Folder download** (recursive) — old client could not
- Multi-select with bulk delete / download / move / restore — the old client rendered checkboxes that did nothing (`ANALYSIS.md` §5.13)
- Drag-and-drop **in** from Explorer (files and folders), drag-and-drop **out** to Explorer (deferred download via `QDrag` + delayed rendering)
- Cut / copy / paste within the vault, and paste from the OS clipboard
- Trash: restore to original path, purge selected, empty all, configurable retention
- **File versions:** view revisions, preview, restore, delete old revisions. Nearly free because chunks are content-addressed and refcounted.
- Sort by any column, filter by type/size/date, saved filters
- Name-collision policy prompt: replace / keep both / skip, with "apply to all"
- Properties, integrity verify, "locate chunks" diagnostics

### 6.4 Interaction and quality

- Keyboard: `F2` rename, `Del` trash, `Shift+Del` purge, `Ctrl+X/C/V`, `Ctrl+A`, `Ctrl+F` search, `Alt+←/→` nav, `Backspace` up, `Enter` open, `Ctrl+Z` undo last tree op.
- **Undo stack** for rename/move/delete (backed by the journal table, V4).
- Full context menus on rows, blank space, and the tree.
- Errors surface as **inline banners and a notification center** — never a modal `alert()` equivalent (`ANALYSIS.md` §5.10). Every error carries a copyable diagnostic ID that maps to a journal entry.
- Transfer dock: per-item progress, speed, ETA, pause/resume/cancel, retry-failed, clear-completed.
- Light/dark theme following the OS, persisted (the old client reset to dark on every reload).
- Accessibility: full keyboard reachability, screen-reader names on all controls, honors OS font scaling.
- Status bar: vault name, node count, total stored size, dedup savings, last snapshot time, last remote backup time, backend health dot.

---

## 7. CLI

Same core, scriptable. Typer + Rich.

```
disbox vault create|open|list|info|export|import|snapshot|restore|rebuild
disbox ls [path]            disbox tree [path]
disbox cp <src> <dst>       disbox mv <src> <dst>      disbox rm [-r] <path>
disbox get <path> [--out]   disbox put <local> [dst] [--recursive]
disbox find <query> [--ext] [--larger-than] [--older-than]
disbox trash list|restore|purge|empty
disbox verify [--deep] [--fix]     disbox gc [--dry-run]
disbox backend probe|info|switch
disbox doctor               # one-shot health report: integrity, orphans, missing chunks
```

`disbox rebuild` and `disbox doctor` are the disaster-recovery entry points and must work against a completely empty local state given only the bot token, channel ID, and passphrase.

---

## 8. Non-functional requirements

| ID | Requirement |
|---|---|
| N1 | Upload saturates the negotiated Discord rate limit; ≥ 8 chunks in flight; crypto never the bottleneck (AES-NI ≫ 1 GB/s/core). |
| N2 | Download ≥ 100 MB/s aggregate on a 1 Gbps link, subject to Discord's CDN. |
| N3 | Peak memory bounded by `concurrency × max_blob × 2`, independent of file size. A 500 GB file must upload without RAM growth. |
| N4 | Every public core function is fully typed; `mypy --strict` passes with zero ignores outside a documented allowlist. |
| N5 | ≥ 85 % line coverage on `disbox.core`, ≥ 95 % on `vault.py`, `crypto.py`, `chunker.py`. |
| N6 | No network call without a timeout. No unbounded retry. No `except:` without a specific type. |
| N7 | Structured logging (`structlog`, JSON to file + pretty to console), redacting tokens and keys. Rotating, 7-day retention. |
| N8 | Ships as a signed Windows installer and a portable zip; no Python install required by the user. |
| N9 | Reproducible builds: `uv.lock` committed, CI builds from the lockfile only. |

---

## 9. Repository layout

```
disbox/
├─ pyproject.toml            # uv-managed, requires-python = ">=3.14"
├─ uv.lock
├─ README.md · SPEC.md · ANALYSIS.md · CHANGELOG.md
├─ src/disbox/
│  ├─ __init__.py
│  ├─ config.py              # pydantic-settings; zero hardcoded URLs/IDs
│  ├─ errors.py
│  ├─ core/
│  │  ├─ vault.py            # SQLite: open, migrate, snapshot, integrity, journal
│  │  ├─ schema/             # migration scripts, versioned
│  │  ├─ models.py           # pydantic domain models
│  │  ├─ filesystem.py       # tree ops, paths, trash, versions, search
│  │  ├─ chunker.py          # FastCDC
│  │  ├─ crypto.py           # Argon2id, HKDF, AES-GCM, BLAKE3, zeroize
│  │  ├─ manifest.py         # Merkle build/verify
│  │  ├─ engine.py           # TransferEngine
│  │  ├─ ratelimit.py
│  │  └─ maintenance.py      # gc, verify, rebuild, remote backup
│  ├─ backends/
│  │  ├─ base.py             # StorageBackend Protocol + BlobRef
│  │  ├─ discord.py
│  │  └─ local.py            # filesystem-backed, for tests
│  ├─ gui/
│  │  ├─ app.py · mainwindow.py · bridge.py
│  │  ├─ models/  views/  dialogs/  widgets/  theme/
│  │  └─ resources/
│  └─ cli/
├─ tests/
│  ├─ unit/  integration/  property/  gui/
│  └─ fixtures/
├─ packaging/                # PyInstaller spec, Inno Setup script
└─ .github/workflows/ci.yml
```

---

## 10. Work breakdown

Each task has an ID, a deliverable, and an acceptance criterion. Milestones are sequential; tasks within a milestone are mostly parallelizable.

### M0 — Bootstrap  *(~2 days)*

- [x] **M0-1** Verify the **Python 3.14 wheel matrix** for `PySide6`, `cryptography`, `blake3`, `argon2-cffi`, `httpx`, `pyinstaller`. Document results in `docs/compat.md`. **If PySide6 has no 3.14 wheel yet, pin to 3.13 and record the upgrade trigger** — do not block on it. *AC: a table of package → 3.14 wheel status, and a decision recorded.*
- [x] **M0-2** Benchmark stock CPython 3.14 vs the free-threaded build on a representative workload (encrypt + hash 1 GB across 8 threads). *AC: numbers in `docs/compat.md`; free-threading adopted only if it wins and all wheels support it.*
- [x] **M0-3** `uv init`; `pyproject.toml` with dependency groups (`core`, `gui`, `cli`, `dev`); commit `uv.lock`.
- [x] **M0-4** Tooling: `ruff` (lint + format), `mypy --strict`, `pytest` + `pytest-asyncio` + `hypothesis` + `pytest-qt`, `pre-commit`.
- [x] **M0-5** `git init` — **the current directory is not a git repository**. Add `.gitignore` covering `*.dbx`, `snapshots/`, `.env`, `dist/`.
- [x] **M0-6** GitHub Actions: lint → typecheck → test → `pip-audit`, on Windows and Linux runners.
- [x] **M0-7** `structlog` setup with a token/key redaction processor. *AC: a test asserts a bot token never appears in log output.*

### M1 — Vault  *(~5 days)*

- [x] **M1-1** Schema §3.3 as versioned migration `0001_initial.sql`; a lightweight migration runner keyed on `PRAGMA user_version`.
- [x] **M1-2** `Vault.open()/create()/close()` with all PRAGMAs from V1 and a single-writer lockfile (PID + hostname + stale detection). *AC: a second process opening the same vault fails with a clear error.*
- [x] **M1-3** Snapshot rotation (V3) using `sqlite3.Connection.backup()`. *AC: snapshots are created without blocking a concurrent read; retention policy honored.*
- [x] **M1-4** Journal writes (V4) on every mutation, behind a decorator so it cannot be forgotten.
- [x] **M1-5** Integrity checks (V5) + `Vault.restore_from_snapshot()`. *AC: a deliberately corrupted vault is detected on open and restorable.*
- [x] **M1-6** Export / import (V8) round-trip. *AC: property test — export → wipe → import produces a byte-identical logical tree.*
- [x] **M1-7** FTS5 trigram index with triggers keeping it in sync with `nodes`. *AC: substring search over 250 k synthetic nodes returns in < 50 ms.*
- [x] **M1-8** Seed/benchmark fixture generating a 250 k-node vault for perf tests.

### M2 — Crypto  *(~3 days)*

- [x] **M2-1** Argon2id KDF with tunable params; auto-calibrate `m`/`t` to ~1 s on the host at vault creation, store the chosen params.
- [x] **M2-2** Master-key wrap/unwrap, `mk_check` verifier, in-memory zeroization on lock/exit.
- [x] **M2-3** HKDF file-key and deterministic per-chunk nonce derivation. *AC: property test — no `(key, nonce)` pair ever repeats across 10⁶ generated chunks.*
- [x] **M2-4** AES-256-GCM seal/open over a `ThreadPoolExecutor`. *AC: throughput > 500 MB/s on 4 threads; benchmark recorded.*
- [x] **M2-5** Chunk header codec (§3.5) with CBOR, versioned, forward-compatible. *AC: a v1 reader rejects a v2 header with a clear error rather than misparsing.*
- [x] **M2-6** Optional OS-keyring KEK storage (Windows DPAPI via `keyring`), opt-in.
- [x] **M2-7** Known-answer tests against RFC/NIST vectors for AES-GCM and HKDF.

### M3 — Chunking & manifest  *(~3 days)*

- [x] **M3-1** FastCDC implementation (or a vetted dependency) with configurable min/avg/max. *AC: > 400 MB/s single-threaded; boundary stability test — inserting 1 byte at offset 0 of a 1 GB file changes < 3 chunks.*
- [x] **M3-2** BLAKE3 chunk hashing over a thread pool.
- [x] **M3-3** Entropy probe → conditional zstd (`compression.zstd`). *AC: text compresses; a JPEG is skipped, verified by a test.*
- [x] **M3-4** Merkle root build + verify. *AC: property test — any single-bit flip in any chunk is detected.*
- [x] **M3-5** Dedup lookup path against `chunks`. *AC: uploading the same 1 GB file twice performs zero second-pass network writes.*

### M4 — Backend abstraction  *(~2 days)*

- [x] **M4-1** `StorageBackend` Protocol: `put`, `get(range)`, `delete`, `bulk_delete`, `exists`, `iter_all`, `max_blob_size`, `probe()`.
- [x] **M4-2** `LocalBackend` writing blobs to a directory — the substrate for fast, network-free tests.
- [x] **M4-3** A shared **conformance test suite** every backend must pass. *AC: `LocalBackend` passes 100 %; `DiscordBackend` later runs the same suite.*

### M5 — Discord backend  *(~5 days)*

- [x] **M5-1** `httpx` client: HTTP/2, pooling, timeouts, `Bot` auth, graceful close.
- [x] **M5-2** Bucket-aware rate limiter keyed on `X-RateLimit-Bucket` + global ceiling. *AC: simulated 429 storms never exceed the limit and never recurse unboundedly.*
- [x] **M5-3** `put` with idempotency keys and `tenacity` retry. *AC: an injected mid-flight failure produces exactly one message, not two.*
- [x] **M5-4** `get` with ranged, streamed reads; lazy URL resolution with TTL cache.
- [x] **M5-5** Expired-URL handling via batch `attachments/refresh-urls`. *AC: a mocked expired URL triggers exactly one refresh and one retry.*
- [x] **M5-6** `delete` / `bulk_delete` with the <14-day constraint handled.
- [x] **M5-7** `probe()` — guild tier read + empirical binary-search size probe, cached to `backends.max_blob`.
- [x] **M5-8** `iter_all()` — paginated channel scan for rebuild.
- [x] **M5-9** Full `respx`-mocked test suite; the M4-3 conformance suite passes. *AC: no test touches the real Discord API.*
- [x] **M5-10** One manual, opt-in live smoke test (`--live`, off by default in CI).

### M6 — Transfer engine  *(~5 days)*

- [x] **M6-1** `TransferEngine.upload()` — TaskGroup fan-out, semaphore, per-chunk checkpointing (T1–T3).
- [x] **M6-2** Resume: detect and continue an interrupted session. *AC: kill the process at 50 % of a 2 GB upload; restart re-sends only the missing chunks.*
- [x] **M6-3** `download()` — parallel fetch, ordered reassembly through a bounded buffer, Merkle verify (T6).
- [x] **M6-4** `read_range()` for previews and future mounting (T7).
- [x] **M6-5** Cancellation tokens across the whole stack (T5). *AC: cancel completes in < 500 ms and leaves a resumable session.*
- [x] **M6-6** Progress events at ≥ 4 Hz with rolling-window ETA (T9). *AC: no `NaN`, no negative, no > 100 % values — the old client produced all three (`ANALYSIS.md` §5.9).*
- [x] **M6-7** Memory ceiling test. *AC: uploading a 50 GB sparse file keeps RSS under the N3 bound.*

### M7 — Filesystem & maintenance  *(~5 days)*

- [x] **M7-1** `FileSystem`: create, rename, move, copy, recursive delete, path resolution, name-collision policy. *AC: renaming `/report/report.txt` behaves correctly — the §5.4 regression test.*
- [x] **M7-2** Trash: soft delete, restore-to-original-path, purge, retention sweep.
- [x] **M7-3** Revisions: create on re-upload, list, restore, prune.
- [x] **M7-4** Recursive folder upload / download with structure preservation.
- [x] **M7-5** GC worker implementing §3.6 exactly. *AC: crash-injection at every step leaves the vault consistent and the next pass completes cleanly.*
- [x] **M7-6** `verify` — existence check for all referenced chunks, plus `--deep` sampled re-hash.
- [x] **M7-7** `rebuild` from channel rescan (§3.5). *AC: delete the vault entirely; rebuild from bot token + channel + passphrase reproduces the full tree, verified against a pre-recorded manifest.*
- [x] **M7-8** Encrypted remote vault backup (V6) + restore-from-remote.
- [x] **M7-9** `doctor` — one-shot health report aggregating integrity, orphans, missing chunks, stale sessions.

### M8 — GUI  *(~10 days)*

- [x] **M8-1** Qt ⇄ asyncio bridge: event loop on a `QThread`, queued signals, clean shutdown. *AC: no cross-thread Qt object access; verified under `pytest-qt`.*
- [x] **M8-2** `QAbstractTableModel` over paged SQLite (U1). *AC: 250 k rows scroll at 60 fps; memory flat.*
- [x] **M8-3** Folder tree with lazy expansion.
- [x] **M8-4** Main window shell: toolbar, breadcrumbs, splitters, status bar, persisted layout.
- [x] **M8-5** Transfer dock: per-item and aggregate progress, pause/resume/cancel/retry.
- [x] **M8-6** All §6.3 filesystem operations wired, with the collision-policy dialog.
- [ ] **M8-7** Drag-and-drop in from Explorer, and out to Explorer with deferred rendering.
- [x] **M8-8** Search bar: debounced FTS5, `ext:`/`size>`/`before:` filters (U4).
- [x] **M8-9** Trash view, Properties dialog, Settings dialog.
- [x] **M8-10** Setup wizard + unlock screen + vault picker.
- [x] **M8-11** Undo stack over the journal.
- [x] **M8-12** Error/notification center with copyable diagnostic IDs; zero blocking modals for errors.
- [x] **M8-13** Theming (light/dark, follows OS) + QSS design tokens + icon set.
- [x] **M8-14** Accessibility pass: keyboard reachability, accessible names, DPI/font scaling.
- [ ] **M8-15** `pytest-qt` tests for every dialog and the main window flows.

### M9 — CLI, packaging, docs  *(~4 days)*

- [ ] **M9-1** Typer CLI covering §7, with Rich progress bars sharing the engine's events.
- [ ] **M9-2** PyInstaller onedir build + Inno Setup installer; portable zip. *AC: installs and runs on a clean Windows 11 VM with no Python present.*
- [ ] **M9-3** First-run experience: vault creation, wizard, sample upload.
- [ ] **M9-4** Docs: README, setup guide, **disaster-recovery runbook** (rebuild, restore, doctor), threat model, ToS notice.
- [ ] **M9-5** Release workflow: tag → build → sign → GitHub Release with checksums.

### M10 — Hardening  *(~3 days)*

- [ ] **M10-1** Chaos suite: kill the process at 20 randomized points during upload, download, delete, and GC; assert vault consistency every time.
- [ ] **M10-2** Long-run soak: 100 GB across 50 k files, measure throughput, memory, and vault growth.
- [ ] **M10-3** Regression tests for **every** defect in `ANALYSIS.md` §5, each named after its section number.
- [ ] **M10-4** Security review: token handling, key zeroization, log redaction, lockfile races, header forgery resistance.
- [ ] **M10-5** Performance profile against every N1–N6 budget; record results in `docs/benchmarks.md`.

**Rough total: ~47 working days** for a single developer, GUI being the largest block. M1–M7 (a fully working CLI-driven system) lands at roughly day 30.

---

## 11. Test strategy

| Layer | Approach |
|---|---|
| Unit | `pytest` per module; `LocalBackend` keeps everything network-free and fast. |
| Property | `hypothesis` on chunker boundaries, Merkle verification, path resolution, export/import round-trips, nonce uniqueness. |
| Integration | Full upload → verify → download → compare cycles against `LocalBackend`, and against `respx`-mocked Discord. |
| Crash | Deterministic fault injection at labelled points; assert the vault invariants after each. |
| GUI | `pytest-qt` for flows; a golden-image check on the main window is optional. |
| Live | A single opt-in `--live` suite against a real throwaway Discord channel, never in CI. |

**Vault invariants** asserted after every mutating test:

1. Every `revision_chunks.chunk_hash` resolves to an existing `chunks` row.
2. `chunks.refcount` equals the true count of referencing `revision_chunks` rows.
3. No node has a `parent_id` cycle, and every non-root node's parent exists.
4. `UNIQUE (parent_id, name, deleted_at)` holds.
5. No `upload_sessions` row is `active` while no transfer is running.

---

## 12. Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| PySide6 has no Python 3.14 wheel yet | Medium | Medium | `M0-1` verifies first; documented fallback to 3.13, upgrade later. Nothing else in the stack is 3.14-specific in a load-bearing way. |
| Discord enforces its ToS on the account | Medium | **Total** | `iter_all` rebuild + encrypted remote vault backups + `StorageBackend` abstraction means migrating to R2/B2 is a background job, not a rewrite. |
| Discord changes attachment limits again | High | Low | Runtime probing (`M5-7`); existing chunks unaffected. |
| CDN URL scheme changes again | Medium | Medium | URLs are never persisted; only `(message_id, attach_id)` is. Refresh path is isolated to one method. |
| Vault file corrupted or lost | Low | High | WAL + snapshots + journal + remote backup + full rescan rebuild. Five independent layers. |
| Argon2id makes unlock feel slow | Medium | Low | Calibrate to ~1 s at creation; optional OS-keyring unlock. |
| Free-threaded build lacks C-extension support | High | Low | Not depended upon; thread pool over GIL-releasing C extensions already parallelizes. |
| Scope creep in the GUI | High | Medium | §6.3 is the frozen v1 list; anything else goes to a v2 backlog. |

---

## 13. What this fixes from v1

Every item in `ANALYSIS.md` §5–§9 maps to a section here. The structural ones:

- **No central server** → the fly.dev single point of total data loss (§7.1) simply does not exist.
- **No browser** → no CORS, so the Chrome extension (§7.8), the allorigins proxy (§6.3), the unpinned CDN script (§6.2), and `localStorage` credentials (§6.1) all disappear as categories.
- **Paths derived from `parent_id`** → the rename corruption (§5.4) and traversal crash (§5.5) are unrepresentable.
- **Refcounts + ordered delete + GC** → orphans and dangling pointers (§7.2) become recoverable rather than permanent.
- **Merkle verification** → silent corruption (§7.6) becomes a hard, located error.
- **Lazy URL resolution + refresh** → expiring links (§5.1) stop being a data-loss event.
- **E2EE** → "we can't read your files" (§6.4, §9.3) becomes literally true, and deletion becomes cryptographic erasure (§9.5).
- **Runtime size probing + backend Protocol** → Discord's unilateral changes (§9.2) and ToS risk (§9.1) become operational events, not extinction events.

---

## 14. Open questions for you

None are blocking — each has a stated default I'll proceed with unless you say otherwise.

1. **Vault portability across machines** — default: manual copy plus a single-writer lockfile. Want a "sync-friendly" mode that detects a foreign lock and offers read-only?
2. **Retention defaults** — trash 30 days, chunk GC grace 24 h, 15 local snapshots, 10 remote backups. Adjust?
3. **Revisions** — default: keep all (cheap via dedup), with manual pruning. Prefer an automatic cap, e.g. last 10?
4. **Second backend in v1** — the Protocol ships either way; `LocalBackend` exists for tests. Want a real S3/R2 backend in v1 as live insurance, or leave it to v2?
5. **Telemetry** — default: none at all, local logs only.
