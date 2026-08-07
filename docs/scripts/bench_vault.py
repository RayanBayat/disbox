"""Measure vault search and integrity-check cost as the node count grows.

Results are recorded in ``docs/benchmarks.md``. Written to a file line by line
so partial progress stays visible on a long run.

Run with::

    uv run python docs/scripts/bench_vault.py
"""

import sqlite3
import tempfile
import time
import uuid
from contextlib import closing
from pathlib import Path

from disbox.core.integrity import check_invariants
from disbox.core.migrations import migrate
from disbox.core.search import search

OUT = Path(__file__).with_name("bench_after.txt")


def emit(line: str) -> None:
    """Append one result line, flushing so long runs stay observable."""
    with OUT.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def run(n: int) -> None:
    """Build a tree of `n` nodes and time search and integrity checking over it."""
    directory = Path(tempfile.mkdtemp())
    with closing(sqlite3.connect(directory / "b.db")) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=FULL")
        conn.execute("PRAGMA foreign_keys=ON")
        migrate(conn)

        # A real tree, not a flat list: every node after the first gets a parent,
        # so the recursive walk actually has depth to traverse.
        ids = [uuid.uuid7().bytes for _ in range(n)]
        rows = [
            (
                ids[i],
                None if i == 0 else ids[(i - 1) // 8],
                f"document-{i:07d}-report-{i % 997}.pdf",
            )
            for i in range(n)
        ]
        started = time.perf_counter()
        with conn:
            conn.executemany(
                "INSERT INTO nodes (id,parent_id,name,kind,created_at,modified_at) "
                "VALUES (?,?,?,'file','2026-01-01','2026-01-01')",
                rows,
            )
        insert_seconds = time.perf_counter() - started
        emit(f"\n=== {n:,} nodes, depth ~{n.bit_length()} (insert {insert_seconds:.1f}s) ===")

        started = time.perf_counter()
        hits = search(conn, "report-42", limit=100)
        elapsed = (time.perf_counter() - started) * 1000
        emit(f"  search              {len(hits):3d} hits {elapsed:9.1f} ms")

        started = time.perf_counter()
        problems = check_invariants(conn)
        elapsed = (time.perf_counter() - started) * 1000
        emit(f"  check_invariants    {len(problems):3d} issues {elapsed:8.1f} ms")


if __name__ == "__main__":
    OUT.write_text("", encoding="utf-8")
    for count in (25_000, 50_000, 100_000, 250_000):
        run(count)
    emit("\nDONE")
