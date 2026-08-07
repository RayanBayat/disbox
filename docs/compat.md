# Python 3.14 Compatibility & Threading Benchmarks

Deliverable for `SPEC.md` tasks **M0-1** (wheel matrix) and **M0-2** (free-threading evaluation).

**Measured:** 2026-08-07 · Windows 11 Pro 26200 · 12 logical CPUs
**Interpreter:** CPython 3.14.3 (`C:\Python314\python.exe`), standard build, GIL enabled
**Tooling:** uv 0.10.5 · git 2.53.0

---

## Decision

> **Target CPython 3.14, standard (GIL-enabled) build. No fallback to 3.13 required.
> The free-threaded build is not adopted.**

Both risks flagged in `SPEC.md` §12 are closed:

| Risk | Verdict |
|---|---|
| "PySide6 has no Python 3.14 wheel yet" (rated *Medium*) | **Did not materialize.** PySide6 6.11.1 ships a cp314 wheel and runs correctly. |
| "Free-threaded build lacks C-extension support" (rated *High*) | **Irrelevant.** The standard build already parallelizes the CPU-bound work — see §3. |

---

## 1. Wheel availability

Resolved with `uv pip install --only-binary=:all: --dry-run` against a clean
3.14.3 virtualenv. `--only-binary=:all:` is the meaningful test: it fails if
only an sdist exists, which would mean a local C toolchain is required.

### Runtime

| Package | Version | Wheel |
|---|---|---|
| PySide6 | 6.11.1 | ✅ |
| cryptography | 50.0.0 | ✅ |
| blake3 | 1.0.9 | ✅ |
| argon2-cffi | 25.1.0 | ✅ |
| httpx | 0.28.1 | ✅ |
| pydantic | 2.13.4 | ✅ |
| pydantic-settings | 2.15.0 | ✅ |
| structlog | 26.1.0 | ✅ |
| typer | 0.27.1 | ✅ |
| rich | 15.0.0 | ✅ |
| tenacity | 9.1.4 | ✅ |
| aiolimiter | 1.2.1 | ✅ |
| cbor2 | 6.1.4 | ✅ |
| platformdirs | 4.11.0 | ✅ |
| keyring | 25.7.0 | ✅ |
| pyinstaller | 6.21.0 | ✅ |

### Development

| Package | Version | Wheel |
|---|---|---|
| pytest | 9.1.1 | ✅ |
| pytest-cov | 7.1.0 | ✅ |
| pytest-asyncio | 1.4.0 | ✅ |
| pytest-qt | 4.5.0 | ✅ |
| pytest-xdist | 3.8.0 | ✅ |
| hypothesis | 6.165.2 | ✅ |
| mypy | 2.3.0 | ✅ |
| ruff | 0.16.2 | ✅ |
| respx | 0.23.1 | ✅ |
| pip-audit | 2.10.1 | ✅ |
| time-machine | 3.3.1 | ✅ |

**27 of 27 packages have a cp314 wheel.** No source builds, no toolchain needed.

## 2. Runtime verification

A wheel existing is not proof it works. Each native package was installed and
exercised:

```
PySide6     : 6.11.1 (Qt 6.11.1)  QApplication + concrete QAbstractTableModel subclass OK
cryptography: 50.0.0              AES-256-GCM encrypt/decrypt roundtrip OK
blake3      : 1.0.9               known-answer vector for b"abc" OK
argon2-cffi : 25.1.0              hash/verify roundtrip OK
cbor2       : 6.1.4               dumps/loads roundtrip OK
```

Stdlib capabilities the design depends on, confirmed present:

| Feature | Status | Used for |
|---|---|---|
| `compression.zstd` (PEP 784) | ✅ | Chunk compression before encryption (`SPEC.md` §4.1) |
| `uuid.uuid7()` | ✅ | Time-ordered primary keys (`SPEC.md` §3.3) |
| `sqlite3` 3.50.4 | ✅ | The vault |
| `sqlite3.Connection.backup()` | ✅ | Non-blocking snapshot rotation (`SPEC.md` V3) |

> **Note.** `QAbstractTableModel` is a C++ abstract class and cannot be
> instantiated directly from Python; it must be subclassed. This is normal and
> is exactly how `SPEC.md` M8-2 uses it.

## 3. Threading benchmark

The architecture in `SPEC.md` §2 claims CPU work can be parallelized with a
plain `ThreadPoolExecutor` because the relevant C extensions release the GIL.
That claim is load-bearing, so it was measured rather than assumed.

**Workload:** BLAKE3 hash + AES-256-GCM encrypt over 512 MiB in 8 MiB chunks —
the real per-chunk upload path.

| Threads | Elapsed | Throughput | Speedup |
|---:|---:|---:|---:|
| 1 | 0.41 s | 1261 MiB/s | 1.00× |
| 2 | 0.22 s | 2315 MiB/s | 1.84× |
| 4 | 0.17 s | 3068 MiB/s | 2.43× |
| 8 | 0.16 s | 3294 MiB/s | 2.61× |

### Interpretation

- **The GIL is genuinely released.** If it were held, every row would sit at
  ~1.00×. Reaching 2.61× proves `cryptography` and `blake3` drop the GIL for
  their C work, so `ThreadPoolExecutor` delivers real parallelism today.
- **Scaling plateaus after ~4 threads.** Sub-linear growth on 12 CPUs points at
  memory bandwidth, not lock contention — 8 MiB buffers at >1 GB/s saturate
  cache and RAM well before the cores run out.
- **Crypto is not the bottleneck, by roughly two orders of magnitude.** At
  3.3 GB/s aggregate against a Discord upload path measured in tens of MB/s,
  the network is ~100× slower. `SPEC.md` requirement **N1** ("crypto never the
  bottleneck") is satisfied with enormous margin.

### Why free-threading is not adopted

`cpython-3.14.3+freethreaded-windows-x86_64` is available through uv, but:

1. There is nothing to gain — the bottleneck is the network, and the standard
   build already parallelizes the CPU work 2.6×.
2. PySide6 support on free-threaded builds is unproven, and the GUI is the
   single largest milestone.
3. Free-threaded wheels are scarcer across the dependency set, reintroducing
   exactly the source-build risk M0-1 was run to eliminate.

**Revisit if** a profile ever shows crypto or hashing as the limiting factor —
currently 100× away from being true.

## 4. Reproducing

```bash
uv venv --python 3.14 probe314
uv pip install --only-binary=:all: PySide6 cryptography blake3 argon2-cffi cbor2
uv run --no-project python docs/scripts/bench_threading.py
```

## 5. Upgrade triggers

Re-run this document when any of these occur:

- PySide6 major release (verify the Qt model/view API and cp314 wheel).
- A profile shows crypto or hashing on the critical path → re-evaluate free-threading.
- Python 3.15 enters beta → re-run the full matrix before adopting.
