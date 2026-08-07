"""The migration runner is the only thing allowed to change schema shape."""

import sqlite3
from collections.abc import Iterator

import pytest

from disbox.core.migrations import LATEST_VERSION, current_version, migrate

EXPECTED_TABLES = frozenset(
    {
        "meta",
        "backends",
        "nodes",
        "revisions",
        "chunks",
        "revision_chunks",
        "upload_sessions",
        "journal",
        "vault_backups",
    }
)


@pytest.fixture
def conn() -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(":memory:")
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        yield connection
    finally:
        # Roll back anything a failed-constraint test left open, so the
        # connection does not raise while being finalised by the collector.
        connection.rollback()
        connection.close()


def table_names(connection: sqlite3.Connection) -> set[str]:
    rows = connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    return {row[0] for row in rows}


class TestVersioning:
    def test_fresh_database_reports_version_zero(self, conn: sqlite3.Connection) -> None:
        assert current_version(conn) == 0

    def test_migrate_reaches_latest_version(self, conn: sqlite3.Connection) -> None:
        assert migrate(conn) == LATEST_VERSION
        assert current_version(conn) == LATEST_VERSION

    def test_latest_version_is_positive(self) -> None:
        assert LATEST_VERSION >= 1, "at least the initial migration must exist"

    def test_migrate_is_idempotent(self, conn: sqlite3.Connection) -> None:
        migrate(conn)
        tables_after_first = table_names(conn)

        assert migrate(conn) == LATEST_VERSION, "re-running must not fail"
        assert table_names(conn) == tables_after_first


class TestSchemaShape:
    def test_all_expected_tables_are_created(self, conn: sqlite3.Connection) -> None:
        migrate(conn)
        assert table_names(conn) >= EXPECTED_TABLES

    def test_full_text_search_index_exists(self, conn: sqlite3.Connection) -> None:
        migrate(conn)
        assert "nodes_fts" in table_names(conn)

    def test_nodes_enforces_unique_names_within_a_directory(self, conn: sqlite3.Connection) -> None:
        migrate(conn)
        conn.execute(
            "INSERT INTO nodes (id, parent_id, name, kind, created_at, modified_at) "
            "VALUES (X'01', NULL, 'dup', 'file', '2026-01-01', '2026-01-01')"
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO nodes (id, parent_id, name, kind, created_at, modified_at) "
                "VALUES (X'02', NULL, 'dup', 'file', '2026-01-01', '2026-01-01')"
            )

    def test_duplicate_names_allowed_once_the_holder_is_trashed(
        self, conn: sqlite3.Connection
    ) -> None:
        migrate(conn)
        conn.execute(
            "INSERT INTO nodes (id, parent_id, name, kind, created_at, modified_at) "
            "VALUES (X'01', NULL, 'notes.txt', 'file', '2026-01-01', '2026-01-01')"
        )
        conn.execute("UPDATE nodes SET deleted_at = '2026-01-02' WHERE id = X'01'")

        # The name is free again; the trashed node still occupies its own row.
        conn.execute(
            "INSERT INTO nodes (id, parent_id, name, kind, created_at, modified_at) "
            "VALUES (X'02', NULL, 'notes.txt', 'file', '2026-01-02', '2026-01-02')"
        )
        surviving = conn.execute("SELECT count(*) FROM nodes WHERE name = 'notes.txt'").fetchone()
        assert surviving[0] == 2

    def test_chunk_reference_must_resolve(self, conn: sqlite3.Connection) -> None:
        migrate(conn)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO revision_chunks (revision_id, idx, chunk_hash) VALUES (1, 0, X'DEAD')"
            )

    def test_node_kind_is_constrained(self, conn: sqlite3.Connection) -> None:
        migrate(conn)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO nodes (id, parent_id, name, kind, created_at, modified_at) "
                "VALUES (X'03', NULL, 'x', 'not-a-kind', '2026-01-01', '2026-01-01')"
            )
