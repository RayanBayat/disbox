# Disbox v2 — Progress Tracker

Live status of the rewrite. Updated as each task lands; the task IDs match
[`SPEC.md`](./SPEC.md) §10.

**Last updated:** 2026-08-07
**Branch:** `main` · **Current milestone:** M0 complete → M1 next

---

## At a glance

| Milestone | Scope | Status | Done |
|---|---|---|---:|
| **M0** | Bootstrap: tooling, git, CI | ✅ Complete | 7 / 7 |
| M1 | Vault (SQLite, snapshots, integrity) | ⚪ Not started | 0 / 8 |
| M2 | Crypto (Argon2id, AES-GCM, headers) | ⚪ Not started | 0 / 7 |
| M3 | Chunking & manifest (FastCDC, Merkle) | ⚪ Not started | 0 / 5 |
| M4 | Backend abstraction | ⚪ Not started | 0 / 3 |
| M5 | Discord backend | ⚪ Not started | 0 / 10 |
| M6 | Transfer engine | ⚪ Not started | 0 / 7 |
| M7 | Filesystem & maintenance | ⚪ Not started | 0 / 9 |
| M8 | GUI (PySide6) | ⚪ Not started | 0 / 15 |
| M9 | CLI, packaging, docs | ⚪ Not started | 0 / 5 |
| M10 | Hardening | ⚪ Not started | 0 / 5 |
| | **Total** | | **7 / 81** |

Legend: ✅ done · 🟡 in progress · ⚪ not started · 🔴 blocked

---

## Health

| Gate | Status | Detail |
|---|---|---|
| Tests | ✅ | 21 passed |
| Types | ✅ | `mypy --strict`, 7 files, no issues |
| Lint | ✅ | `ruff check` clean |
| Format | ✅ | `ruff format --check` clean |
| CI | ✅ | Windows + Linux matrix; lockfile audited, 0 vulnerabilities |
| Coverage | ⚪ | Not enforced until M1 lands real logic |

---

## M0 — Bootstrap

| Task | Description | Status | Notes |
|---|---|---|---|
| M0-1 | Verify Python 3.14 wheel matrix | ✅ | 27/27 packages have cp314 wheels. PySide6 6.11.1 verified running. See [`docs/compat.md`](./docs/compat.md) |
| M0-2 | Benchmark free-threading vs standard | ✅ | 2.61× speedup on standard build proves GIL release. Free-threading **not adopted** |
| M0-3 | `uv init`, `pyproject.toml`, lockfile | ✅ | Python 3.14, src layout, PEP 621 + 735 |
| M0-4 | Tooling: ruff, mypy, pytest | ✅ | ruff w/ 20 rule sets; `mypy --strict`; pytest w/ `filterwarnings = error` |
| M0-5 | `git init` + `.gitignore` | ✅ | Done first, before any other file |
| M0-6 | GitHub Actions CI | ✅ | Windows + Linux matrix, plus a lockfile audit job |
| M0-7 | structlog with token redaction | ✅ | 18 tests; verified scrubbing tokens out of live tracebacks |

### Decisions made

| # | Decision | Rationale |
|---|---|---|
| 1 | **CPython 3.14.3, standard build** | Every dependency has a cp314 wheel; the 3.13 fallback is unnecessary |
| 2 | **Free-threaded build rejected** | Standard build already parallelizes 2.61×; crypto runs ~100× faster than the network, so there is nothing to win |
| 3 | **Legacy JS preserved in git, then removed** | Commit `be58740` keeps it recoverable forever; the working tree stays clean |
| 4 | **Runtime deps added per-milestone** | The manifest never lists a package the code does not import |
| 5 | **Markdown excluded from ruff** | Ruff formats Python inside md fences and was rewriting the annotated sketches in `ANALYSIS.md` |
| 6 | **Imports pinned to top of file** | `E402` + `PLC0415` enforced in CI, verified firing against a probe file |
| 7 | **Redaction is a processor, not a call-site choice** | Opt-in redaction fails the first time someone forgets; running it on every event after `format_exc_info` also covers tracebacks |
| 8 | **CI audits the exported lockfile, not the environment** | `pip-audit --strict` cannot resolve our own unpublished package and treats that as a failure |

### Open questions

Carried from `SPEC.md` §14 — none blocking, each has a working default:

1. Vault portability across machines — default: manual copy + single-writer lockfile
2. Retention defaults — trash 30d, GC grace 24h, 15 local snapshots, 10 remote
3. Revisions — default: keep all, manual pruning
4. Second backend in v1 — default: Protocol only, `LocalBackend` for tests
5. Telemetry — default: none

---

## Commit log

| Commit | Message |
|---|---|
| `a7054a9` | chore: initialize repository |
| `c3edebb` | chore: normalize line endings via gitattributes |
| `be58740` | chore: import legacy web client |
| `019426d` | docs: add legacy analysis and v2 specification |
| `bf6c141` | chore: remove legacy web client |
| `b937419` | chore: scaffold python project with uv, ruff, mypy, and pytest |
| `042588a` | docs: record python 3.14 compatibility and threading benchmarks |
| `7e6ecdf` | docs: add progress tracker |
| `06bd09e` | ci: add quality and dependency-audit workflows |
| `f2055b5` | feat(log): add structured logging with mandatory secret redaction |

---

## How to run

```bash
uv sync --group dev          # install
uv run pytest -q             # tests
uv run mypy                  # types
uv run ruff check .          # lint
uv run ruff format .         # format
```

---

## Notes and gotchas

- **`QAbstractTableModel` is abstract** and must be subclassed — relevant to M8-2.
- **Ruff formats Python inside markdown fences.** Excluded via `extend-exclude = ["*.md"]`.
- **`filterwarnings = ["error"]`** in pytest: any warning fails the suite. Deliberate — it caught a deprecated `argon2.__version__` access during M0-1.
- **Line endings** normalized to LF via `.gitattributes`; Windows scripts keep CRLF.
- Vault files (`*.dbx`) and `snapshots/` are gitignored — they hold wrapped keys.
