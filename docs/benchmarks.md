# Vault Benchmarks

Measured on Windows 11 Pro 26200, 12 logical CPUs, CPython 3.14.3, SQLite 3.50.4.
Reproduce with the scripts under `docs/scripts/`.

---

## Search (SPEC.md U4: < 50 ms on 250k nodes)

FTS5 trigram index, `LIMIT 100`.

| Nodes | Unique match | Many matches | Matches everything |
|---:|---:|---:|---:|
| 25,000 | 0.5 ms | 1.2 ms | 0.4 ms |
| 50,000 | 0.5 ms | 1.4 ms | 0.5 ms |
| 100,000 | 0.6 ms | 1.8 ms | 0.6 ms |
| 250,000 | — | 1.4 ms | — |

**Comfortably inside budget**, by roughly 30×. Notably a query matching *every*
row is no slower than a unique one: SQLite short-circuits at the `LIMIT` rather
than materialising all matches first.

## Integrity checking

`check_invariants` covers refcount drift, orphaned chunk references, missing
parents, and parent cycles.

| Nodes | Before fix (cycle check alone) | After fix (whole check) |
|---:|---:|---:|
| 25,000 | 111,048 ms | **85.8 ms** |
| 50,000 | 465,783 ms | **204.7 ms** |
| 100,000 | did not finish | **391.2 ms** |
| 250,000 | did not finish | **1,000.9 ms** |

Measured against a real tree (fan-out 8, depth ~18), not a flat list, so the
recursive traversal has genuine depth to walk.

### What was wrong

The first implementation was **quadratic** -- 2× the rows cost 4.2× the time --
for two compounding reasons:

1. **`NOT IN (SELECT id FROM cte)`.** A CTE carries no index, so SQLite rescanned
   the entire reachable set once per candidate row.
2. **The parent index was partial.** `idx_nodes_parent` was declared
   `WHERE deleted_at IS NULL`, so it could not serve the cycle check, which must
   consider trashed nodes too. Each level of recursion degraded to a table scan.

### The fix

* Migration `0003` replaces the partial index with a composite
  `(parent_id, deleted_at)`, which serves both ancestry walks and directory
  listings from one structure.
* `_find_cycles` materialises the reachable set into a temporary table with a
  primary key, then anti-joins against it.

Result is linear: 25k→50k costs 2.4×, 50k→100k costs 1.9×, 100k→250k costs 2.6×
for 2.5× the data.

## Bulk insert

Includes FTS5 trigram trigger maintenance and `synchronous=FULL`.

| Nodes | Time |
|---:|---:|
| 25,000 | 1.6 s |
| 50,000 | 3.7 s |
| 100,000 | 7.8 s |
| 250,000 | 20.5 s |

The trigram triggers cost roughly 8–9× versus an unindexed insert, measured
separately and stable across sizes. That is the price of instant substring
search and is paid once per write, not per query.

## Threading

See [`compat.md`](./compat.md) for the AES-GCM and BLAKE3 GIL-release
measurements underpinning the transfer engine design.

## Method note

An early attempt at these numbers appeared to hang. Two mistakes compounded:
the benchmark printed without `flush=True`, so a redirected run showed an empty
file while work was in progress, and the quadratic cycle check genuinely was
taking hours. Benchmarks here now write results to a file line by line, so
partial progress is always visible.
