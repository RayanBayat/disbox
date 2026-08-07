-- Make ancestry traversal indexable.
--
-- idx_nodes_parent was partial (WHERE deleted_at IS NULL). That serves listing
-- a directory, but it cannot serve any query that walks the whole tree --
-- notably the cycle check, which must consider trashed nodes too, since a cycle
-- among them is still corruption.
--
-- Without a usable index the recursive traversal degrades to a full table scan
-- per level. Measured on this schema: 25k nodes took 111 s and 50k took 466 s,
-- roughly 4x the time for 2x the rows, which is the quadratic signature.
--
-- A composite index on (parent_id, deleted_at) serves both callers: the leading
-- column covers ancestry walks, and the second lets a directory listing filter
-- trashed rows from the index without touching the table.

DROP INDEX IF EXISTS idx_nodes_parent;

CREATE INDEX idx_nodes_parent ON nodes (parent_id, deleted_at);
