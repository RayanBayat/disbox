-- Keep the full-text index in step with the node tree automatically.
--
-- nodes_fts is an external-content FTS5 table: it stores only the index, and
-- reads the text back from nodes. That saves duplicating every filename, but it
-- means SQLite will NOT maintain it -- the index silently rots unless the
-- triggers below mirror every change.
--
-- Doing this with triggers rather than in application code is deliberate. The
-- vault is also written by migrations, imports, and future maintenance jobs;
-- anything that reaches the table directly stays correct for free, and nothing
-- has to remember to reindex.
--
-- The 'delete' command form is required for external-content tables: FTS5
-- cannot recover the old terms itself, so the previous text must be handed
-- back before the row changes.

CREATE TRIGGER nodes_after_insert AFTER INSERT ON nodes BEGIN
    INSERT INTO nodes_fts (rowid, name) VALUES (new.rowid, new.name);
END;

CREATE TRIGGER nodes_after_delete AFTER DELETE ON nodes BEGIN
    INSERT INTO nodes_fts (nodes_fts, rowid, name) VALUES ('delete', old.rowid, old.name);
END;

CREATE TRIGGER nodes_after_update AFTER UPDATE OF name ON nodes BEGIN
    INSERT INTO nodes_fts (nodes_fts, rowid, name) VALUES ('delete', old.rowid, old.name);
    INSERT INTO nodes_fts (rowid, name) VALUES (new.rowid, new.name);
END;

-- Backfill anything inserted before these triggers existed.
INSERT INTO nodes_fts (rowid, name) SELECT rowid, name FROM nodes;
