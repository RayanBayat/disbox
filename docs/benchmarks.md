# Vault Benchmarks

Measured on Windows 11 Pro 26200, 12 logical CPUs, CPython 3.14.3, SQLite 3.50.4.
Reproduce with the scripts under `docs/scripts/`.

---

## Transfer pipeline

Local backend, so the numbers describe this codebase rather than a network.
Incompressible random data, which is the honest worst case: nothing compresses
and nothing deduplicates. Production chunk spec (256 KiB / 1 MiB / 4 MiB),
median of three runs. Reproduce with `docs/scripts/bench_transfer.py`.

| Size | Chunking | Upload | Download |
|---:|---:|---:|---:|
| 1 MiB | 7.8 MiB/s | 6.8 MiB/s | 85.2 MiB/s |
| 8 MiB | 5.5 MiB/s | 5.3 MiB/s | 280.4 MiB/s |
| 64 MiB | 4.7 MiB/s | 4.8 MiB/s | 423.5 MiB/s |

### Upload is chunk-bound, and chunking is slow

Download runs 60–90× faster than upload, and upload tracks chunking almost
exactly. Profiling 8 MiB confirms where it goes:

```
7 calls   1.607 s   chunker.py:102(_cut_point)     <- 99% of 1.624 s total
```

`_cut_point` is the per-byte Gear-hash rolling loop, in pure Python. Neither
encryption nor storage is the constraint; the content-defined boundary search
is. At ~5 MiB/s a 1 GB upload spends about **3.5 minutes** on chunking alone
before a byte is sent.

**This is a real limit on a stated goal, and it is not fixed.** Options, in
rough order of payoff:

1. **A native FastCDC** (`fastcdc` on PyPI, or a small Rust/C extension). Two
   orders of magnitude available. Adds a build dependency.
2. **Vectorise the scan** with `numpy` over the rolling hash. Perhaps 10×,
   pure-Python deployment preserved.
3. **Larger `avg_size`.** Fewer boundary searches, at the cost of coarser
   deduplication. Cheapest, and the least effective.

**Any of them changes where chunk boundaries fall.** Existing vaults would stop
deduplicating against newly uploaded copies of the same file — old chunks stay
valid and readable, but the saving is lost until everything is re-uploaded. That
makes this a decision about migration, not only about speed.

Download needs no such work: it is already faster than any plausible network.

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
