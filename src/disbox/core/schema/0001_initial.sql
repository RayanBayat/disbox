-- Disbox vault, initial schema. See SPEC.md section 3.3.
--
-- Design rules encoded here, each closing a defect catalogued in ANALYSIS.md:
--   * Paths are DERIVED from parent_id, never stored as strings, so a rename
--     cannot corrupt a path by substring replacement (ANALYSIS.md 5.4).
--   * nodes.version supports optimistic concurrency instead of last-writer-wins
--     (ANALYSIS.md 7.7).
--   * chunks.refcount drives safe garbage collection, so deleting a file can
--     never orphan a blob or dangle a pointer (ANALYSIS.md 7.2).
--   * deleted_at gives a recoverable trash rather than destructive delete.
--   * backends.max_blob is probed at runtime, never hardcoded, so a provider
--     changing its upload limit is a config event (ANALYSIS.md 9.2).

-- Single row describing this vault and how to unwrap its master key.
CREATE TABLE meta (
    vault_id       BLOB    NOT NULL PRIMARY KEY,          -- UUIDv7
    schema_version INTEGER NOT NULL,
    created_at     TEXT    NOT NULL,
    kdf_salt       BLOB    NOT NULL,
    kdf_params     TEXT    NOT NULL,                      -- json: {t, m, p}
    wrapped_mk     BLOB    NOT NULL,                      -- AES-GCM(KEK, master_key)
    mk_check       BLOB    NOT NULL,                      -- verifies passphrase cheaply
    singleton      INTEGER NOT NULL DEFAULT 1 CHECK (singleton = 1),
    UNIQUE (singleton)
);

-- A blob store. Discord is one implementation; local disk is another.
CREATE TABLE backends (
    id         INTEGER PRIMARY KEY,
    kind       TEXT    NOT NULL CHECK (kind IN ('discord', 'local')),
    label      TEXT    NOT NULL,
    config_enc BLOB    NOT NULL,                          -- AES-GCM: token, channel id
    max_blob   INTEGER NOT NULL CHECK (max_blob > 0),
    probed_at  TEXT,
    is_default INTEGER NOT NULL DEFAULT 0 CHECK (is_default IN (0, 1))
);

-- The virtual filesystem tree.
CREATE TABLE nodes (
    id          BLOB    NOT NULL PRIMARY KEY,             -- UUIDv7
    parent_id   BLOB    REFERENCES nodes (id) ON DELETE RESTRICT,
    name        TEXT    NOT NULL CHECK (name <> '' AND name NOT LIKE '%/%'),
    kind        TEXT    NOT NULL CHECK (kind IN ('dir', 'file')),
    size        INTEGER NOT NULL DEFAULT 0 CHECK (size >= 0),
    created_at  TEXT    NOT NULL,
    modified_at TEXT    NOT NULL,
    deleted_at  TEXT,
    version     INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
    current_rev INTEGER,
    mime        TEXT
);

-- Sibling names must be unique among LIVE nodes.
--
-- A plain UNIQUE (parent_id, name, deleted_at) does not work: SQL treats NULLs
-- as distinct in unique constraints, so every live node (deleted_at IS NULL)
-- and every top-level node (parent_id IS NULL) would slip through, which is the
-- normal case rather than an edge case. COALESCE collapses the NULL parent to a
-- sentinel, and the partial WHERE deliberately excludes trashed rows so a name
-- becomes available again once its holder is deleted.
CREATE UNIQUE INDEX idx_nodes_unique_live_sibling
    ON nodes (COALESCE(parent_id, X'00'), name)
    WHERE deleted_at IS NULL;

CREATE INDEX idx_nodes_parent ON nodes (parent_id) WHERE deleted_at IS NULL;
CREATE INDEX idx_nodes_trash ON nodes (deleted_at) WHERE deleted_at IS NOT NULL;

-- File history. Cheap, because revisions share deduplicated chunks.
CREATE TABLE revisions (
    id          INTEGER PRIMARY KEY,
    node_id     BLOB    NOT NULL REFERENCES nodes (id) ON DELETE CASCADE,
    created_at  TEXT    NOT NULL,
    size        INTEGER NOT NULL CHECK (size >= 0),
    merkle_root BLOB    NOT NULL,
    chunk_count INTEGER NOT NULL CHECK (chunk_count >= 0)
);

CREATE INDEX idx_revisions_node ON revisions (node_id);

-- Content-addressed blobs. hash is of the PLAINTEXT chunk, so dedup works
-- despite every stored copy being encrypted under a different nonce.
CREATE TABLE chunks (
    hash        BLOB    NOT NULL PRIMARY KEY,             -- BLAKE3 of plaintext
    size        INTEGER NOT NULL CHECK (size > 0),
    stored_size INTEGER NOT NULL CHECK (stored_size > 0), -- after zstd + AES-GCM
    backend_id  INTEGER NOT NULL REFERENCES backends (id),
    message_id  TEXT    NOT NULL,
    attach_id   TEXT    NOT NULL,
    refcount    INTEGER NOT NULL DEFAULT 0 CHECK (refcount >= 0),
    verified_at TEXT,
    UNIQUE (backend_id, message_id, attach_id)
);

-- Partial index: the GC only ever scans unreferenced chunks.
CREATE INDEX idx_chunks_gc ON chunks (refcount) WHERE refcount = 0;

-- Ordered manifest binding a revision to its chunks.
CREATE TABLE revision_chunks (
    revision_id INTEGER NOT NULL REFERENCES revisions (id) ON DELETE CASCADE,
    idx         INTEGER NOT NULL CHECK (idx >= 0),
    chunk_hash  BLOB    NOT NULL REFERENCES chunks (hash),
    PRIMARY KEY (revision_id, idx)
);

CREATE INDEX idx_revision_chunks_hash ON revision_chunks (chunk_hash);

-- Resumable uploads survive a crash or a restart.
CREATE TABLE upload_sessions (
    id          BLOB NOT NULL PRIMARY KEY,
    node_id     BLOB REFERENCES nodes (id) ON DELETE CASCADE,
    source_path TEXT NOT NULL,
    total_size  INTEGER CHECK (total_size IS NULL OR total_size >= 0),
    state       TEXT NOT NULL CHECK (state IN ('active', 'paused', 'failed', 'done')),
    completed   TEXT NOT NULL DEFAULT '{}',               -- json: {chunk_idx: hash}
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE INDEX idx_upload_sessions_state ON upload_sessions (state);

-- Append-only forensic record of every mutation. Also backs the undo stack.
CREATE TABLE journal (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        TEXT NOT NULL,
    op        TEXT NOT NULL,
    target_id BLOB,
    payload   TEXT
);

CREATE INDEX idx_journal_ts ON journal (ts);

-- Encrypted copies of this vault stored on the backend, so losing the local
-- file costs a download rather than the data.
CREATE TABLE vault_backups (
    id         INTEGER PRIMARY KEY,
    created_at TEXT    NOT NULL,
    message_id TEXT    NOT NULL,
    size       INTEGER NOT NULL CHECK (size > 0),
    node_count INTEGER NOT NULL CHECK (node_count >= 0)
);

-- Trigram index so substring search stays instant on a large tree; the old
-- client walked the whole tree in the browser on every keystroke.
CREATE VIRTUAL TABLE nodes_fts USING fts5 (
    name,
    content = 'nodes',
    content_rowid = 'rowid',
    tokenize = 'trigram'
);
