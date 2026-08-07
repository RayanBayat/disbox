"""Guard against performance regressions on a large vault.

Two separate components in this codebase turned out to be accidentally
quadratic -- the cycle check and the directory listing -- and both looked fine
on the small vaults the unit tests use. Nothing but a large fixture catches
that class of bug.

**The thresholds here are deliberately loose.** CI hardware varies by an order
of magnitude, so tight budgets would produce flaky failures that get muted, and
a muted test guards nothing. Each ceiling sits far above the measured healthy
value and far below the broken one, which is the range that matters: the real
regressions overshot by 100x or more, not by 20%. The SPEC's actual UI budgets
(16 ms directory switch, 50 ms search) are verified by hand in
``docs/benchmarks.md``; these tests exist to catch collapse, not drift.

Marked ``slow`` and excluded from the default run. Execute with::

    uv run pytest -m slow
"""

import os
import time
import uuid
from collections.abc import Iterator

import pytest
from PySide6.QtCore import Qt

from disbox.core.integrity import check_invariants
from disbox.core.search import search
from disbox.core.vault import Vault
from disbox.gui.models.file_table import Column, FileTableModel
from tests.unit.test_vault import KEYS

pytestmark = pytest.mark.slow

# Overridable so a developer can get a quick signal without the full build.
NODE_COUNT = int(os.environ.get("DISBOX_PERF_NODES", "250000"))

# The fixture needs both shapes, because they stress different things: one very
# large flat directory for the table model, and a deep fan-out tree for
# ancestry traversal and the cycle check. Building only the tree leaves the root
# nearly empty, which silently reduced the table tests to a one-row directory.
FAN_OUT = 8
FLAT_FRACTION = 0.6

LISTING_CEILING_MS = 500.0
PAINT_CEILING_MS = 250.0
SEARCH_CEILING_MS = 250.0
INVARIANTS_CEILING_MS = 15_000.0


@pytest.fixture(scope="session")
def large_vault(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Vault]:
    """Build one big vault for the whole session; it costs ~20 s at 250k nodes."""
    directory = tmp_path_factory.mktemp("perf")
    flat_count = max(1, int(NODE_COUNT * FLAT_FRACTION))

    def parent_of(index: int) -> bytes | None:
        """Place the first slice at the root, and chain the rest into a tree."""
        if index < flat_count:
            return None
        return ids[flat_count + (index - flat_count) // FAN_OUT - 1]

    with Vault.create(directory / "large.dbx", KEYS) as vault:
        ids = [uuid.uuid7().bytes for _ in range(NODE_COUNT)]
        with vault.connection as conn:
            conn.executemany(
                "INSERT INTO nodes (id, parent_id, name, kind, size, created_at, modified_at) "
                "VALUES (?, ?, ?, ?, ?, '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')",
                [
                    (
                        ids[index],
                        parent_of(index),
                        f"document-{index:07d}-report-{index % 997}.pdf",
                        # Everything in the tree slice must be able to hold
                        # children, so the fan-out actually materialises.
                        "dir" if index >= flat_count - 1 else "file",
                        index * 1024,
                    )
                    for index in range(NODE_COUNT)
                ],
            )
        yield vault


def elapsed_ms(operation: object) -> float:
    """Time a zero-argument callable, in milliseconds."""
    started = time.perf_counter()
    operation()  # type: ignore[operator]
    return (time.perf_counter() - started) * 1000


class TestVaultScale:
    def test_the_fixture_really_is_large(self, large_vault: Vault) -> None:
        """Without this the whole module could pass against an empty vault."""
        count = large_vault.connection.execute("SELECT count(*) FROM nodes").fetchone()[0]
        assert count == NODE_COUNT

    def test_the_fixture_has_a_large_root_directory(self, large_vault: Vault) -> None:
        """The table tests are meaningless against a nearly empty root."""
        rows = large_vault.connection.execute(
            "SELECT count(*) FROM nodes WHERE parent_id IS NULL AND deleted_at IS NULL"
        ).fetchone()[0]
        assert rows > NODE_COUNT // 2, f"root holds only {rows} nodes"

    def test_the_fixture_has_real_depth(self, large_vault: Vault) -> None:
        """Ancestry traversal is only exercised if the tree is not flat."""
        depth = large_vault.connection.execute(
            """
            WITH RECURSIVE walk(id, level) AS (
                SELECT id, 0 FROM nodes WHERE parent_id IS NULL
                UNION ALL
                SELECT n.id, w.level + 1 FROM nodes AS n JOIN walk AS w ON n.parent_id = w.id
            )
            SELECT max(level) FROM walk
            """
        ).fetchone()[0]
        assert depth >= 3, f"tree is only {depth} levels deep"

    def test_search_does_not_collapse(self, large_vault: Vault) -> None:
        took = elapsed_ms(lambda: search(large_vault.connection, "report-42", limit=100))
        assert took < SEARCH_CEILING_MS, f"search took {took:.0f} ms"

    def test_search_for_a_term_matching_everything_is_still_bounded(
        self, large_vault: Vault
    ) -> None:
        """A LIMIT must short-circuit rather than materialising every match."""
        took = elapsed_ms(lambda: search(large_vault.connection, "document", limit=100))
        assert took < SEARCH_CEILING_MS, f"search took {took:.0f} ms"

    def test_integrity_check_does_not_collapse(self, large_vault: Vault) -> None:
        """This was 111 s at 25k nodes before the cycle check was made linear."""
        took = elapsed_ms(lambda: check_invariants(large_vault.connection))
        assert took < INVARIANTS_CEILING_MS, f"check_invariants took {took:.0f} ms"

    def test_the_large_vault_is_actually_consistent(self, large_vault: Vault) -> None:
        assert check_invariants(large_vault.connection) == []


class TestQueryPlans:
    """Assert the plans directly, which timing cannot do reliably.

    A ceiling in milliseconds is a blunt instrument: it has to be loose enough
    to survive slow CI, which leaves room for a real regression to hide. The
    plan is exact and hardware-independent -- if SQLite starts sorting a
    directory again, or stops using the ancestry index, that shows up here
    regardless of how fast the machine is.
    """

    @staticmethod
    def plan(vault: Vault, sql: str, params: tuple[object, ...] = ()) -> str:
        rows = vault.connection.execute(f"EXPLAIN QUERY PLAN {sql}", params).fetchall()
        return " | ".join(str(row[3]) for row in rows)

    def test_directory_listing_is_not_sorted_at_query_time(self, large_vault: Vault) -> None:
        """The whole point of idx_nodes_listing is to avoid this sort."""
        model = FileTableModel(large_vault)
        model.set_directory(None)
        # Reaching into the model keeps this checking the statement actually
        # issued, rather than a copy that could drift out of step with it.
        where, params = model._directory_filter()
        sql = (
            f"SELECT id, name, kind, size, modified_at FROM nodes WHERE {where} "  # noqa: S608
            "ORDER BY (kind = 'dir') DESC, name COLLATE NOCASE LIMIT 200 OFFSET 0"
        )

        plan = self.plan(large_vault, sql, params)
        assert "TEMP B-TREE" not in plan.upper(), f"listing fell back to a sort: {plan}"
        assert "idx_nodes_listing" in plan, f"listing index unused: {plan}"

    def test_ancestry_walk_uses_an_index(self, large_vault: Vault) -> None:
        """Without this the cycle check degrades to a scan per level."""
        plan = self.plan(
            large_vault,
            "SELECT n.id FROM nodes AS n JOIN nodes AS p ON p.id = n.parent_id "
            "WHERE n.parent_id = ?",
            (b"\x00" * 16,),
        )
        assert "SCAN nodes" not in plan, f"ancestry walk scans the table: {plan}"

    def test_search_uses_the_full_text_index(self, large_vault: Vault) -> None:
        plan = self.plan(
            large_vault,
            "SELECT n.id FROM nodes_fts JOIN nodes AS n ON n.rowid = nodes_fts.rowid "
            "WHERE nodes_fts MATCH ? LIMIT 100",
            ("report",),
        )
        assert "nodes_fts" in plan, f"search bypassed the index: {plan}"


class TestTableModelScale:
    def test_opening_a_directory_does_not_collapse(self, large_vault: Vault) -> None:
        model = FileTableModel(large_vault)
        took = elapsed_ms(lambda: model.set_directory(None))
        assert took < LISTING_CEILING_MS, f"set_directory took {took:.0f} ms"

    def test_painting_the_last_screen_does_not_collapse(self, large_vault: Vault) -> None:
        """Cost must not grow with scroll depth; it did before the listing index."""
        model = FileTableModel(large_vault)
        model.set_directory(None)
        rows = model.rowCount()
        # An assertion rather than a skip: too few rows means the fixture is
        # broken, and a silently skipped guard protects nothing.
        assert rows >= 40, f"root directory holds only {rows} rows"

        def paint_last_screen() -> None:
            for row in range(rows - 40, rows):
                for column in Column:
                    model.data(model.index(row, column), Qt.ItemDataRole.DisplayRole)

        took = elapsed_ms(paint_last_screen)
        assert took < PAINT_CEILING_MS, f"painting the last screen took {took:.0f} ms"

    def test_scrolling_does_not_accumulate_rows_in_memory(self, large_vault: Vault) -> None:
        """Peak memory must be bounded by the cache, not by directory size."""
        model = FileTableModel(large_vault, page_size=200)
        model.set_directory(None)
        for row in range(0, min(model.rowCount(), 5000), 37):
            model.data(model.index(row, Column.NAME), Qt.ItemDataRole.DisplayRole)

        assert model.rows_cached() <= 200 * 8, f"{model.rows_cached()} rows resident"
