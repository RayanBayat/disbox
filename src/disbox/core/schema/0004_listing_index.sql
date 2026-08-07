-- Serve a directory listing straight from an index, in display order.
--
-- The file table sorts folders first, then by name case-insensitively. Without
-- an index in that exact shape SQLite builds a temporary B-tree and sorts the
-- whole directory on every page fetch, which at 250k rows measured 90 ms for
-- the first page and 319 ms deep into a scroll -- roughly twenty times the
-- 16.7 ms a 60 fps frame allows.
--
-- The index must match the ORDER BY exactly to be usable for ordering: the
-- same expression, the same direction, and the same collation. It is partial
-- on deleted_at because trashed rows never appear in a listing, which keeps it
-- smaller and lets it cover the filter too.
--
-- Note this only helps if the query avoids OR in its WHERE clause. Matching a
-- NULL parent with `(? IS NULL AND parent_id IS NULL) OR parent_id = ?` forces
-- a multi-index OR and reintroduces the sort, so callers issue a separate
-- statement for the root instead.

CREATE INDEX idx_nodes_listing
    ON nodes (parent_id, (kind = 'dir') DESC, name COLLATE NOCASE)
    WHERE deleted_at IS NULL;
