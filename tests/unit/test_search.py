"""Search must stay in step with the tree, automatically."""

import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

from disbox.core.search import search
from disbox.core.vault import Vault
from tests.unit.test_vault import KEYS


@pytest.fixture
def vault(tmp_path: Path) -> Iterator[Vault]:
    with Vault.create(tmp_path / "test.dbx", KEYS) as vault:
        yield vault


def add(vault: Vault, name: str, *, kind: str = "file") -> uuid.UUID:
    """Insert a node and return its id."""
    node_id = uuid.uuid7()
    with vault.connection as conn:
        conn.execute(
            "INSERT INTO nodes (id, parent_id, name, kind, created_at, modified_at) "
            "VALUES (?, NULL, ?, ?, '2026-01-01', '2026-01-01')",
            (node_id.bytes, name, kind),
        )
    return node_id


def names(vault: Vault, query: str, **kwargs: object) -> list[str]:
    return [hit.name for hit in search(vault.connection, query, **kwargs)]  # type: ignore[arg-type]


class TestIndexStaysInSync:
    def test_inserted_node_becomes_findable(self, vault: Vault) -> None:
        add(vault, "quarterly-report.pdf")
        assert names(vault, "quarterly") == ["quarterly-report.pdf"]

    def test_renamed_node_is_found_under_its_new_name_only(self, vault: Vault) -> None:
        node_id = add(vault, "draft.txt")
        with vault.connection as conn:
            conn.execute("UPDATE nodes SET name = 'final.txt' WHERE id = ?", (node_id.bytes,))

        assert names(vault, "final") == ["final.txt"]
        assert names(vault, "draft") == [], "the stale name must leave the index"

    def test_deleted_node_leaves_the_index(self, vault: Vault) -> None:
        node_id = add(vault, "temporary.log")
        with vault.connection as conn:
            conn.execute("DELETE FROM nodes WHERE id = ?", (node_id.bytes,))

        assert names(vault, "temporary") == []

    def test_index_survives_many_mutations(self, vault: Vault) -> None:
        ids = [add(vault, f"file-{index:03d}.txt") for index in range(50)]
        with vault.connection as conn:
            for node_id in ids[:25]:
                conn.execute("DELETE FROM nodes WHERE id = ?", (node_id.bytes,))
            for node_id in ids[25:]:
                conn.execute(
                    "UPDATE nodes SET name = 'kept-' || name WHERE id = ?", (node_id.bytes,)
                )

        assert len(names(vault, "kept", limit=100)) == 25
        assert names(vault, "file-000") == []


class TestMatching:
    def test_substring_in_the_middle_matches(self, vault: Vault) -> None:
        """Trigram indexing is chosen precisely so this works."""
        add(vault, "holiday-photos-2026.zip")
        assert names(vault, "photos") == ["holiday-photos-2026.zip"]

    def test_matching_is_case_insensitive(self, vault: Vault) -> None:
        add(vault, "Invoice.PDF")
        assert names(vault, "invoice") == ["Invoice.PDF"]

    def test_short_queries_still_work(self, vault: Vault) -> None:
        """Trigram tokenising needs three characters; shorter must not fail."""
        add(vault, "ab-notes.txt")
        assert names(vault, "ab") == ["ab-notes.txt"]

    def test_query_with_fts_syntax_characters_is_treated_literally(self, vault: Vault) -> None:
        add(vault, 'weird "quoted" name.txt')
        assert names(vault, '"quoted"') == ['weird "quoted" name.txt']

    def test_query_with_operators_does_not_error(self, vault: Vault) -> None:
        add(vault, "normal.txt")
        for query in ("NOT", "a OR b", "x AND y", "*", "^start", "col:val"):
            search(vault.connection, query)  # must not raise

    def test_empty_query_returns_nothing(self, vault: Vault) -> None:
        add(vault, "something.txt")
        assert names(vault, "   ") == []

    def test_limit_is_honoured(self, vault: Vault) -> None:
        for index in range(20):
            add(vault, f"report-{index:02d}.txt")
        assert len(names(vault, "report", limit=5)) == 5


class TestTrashHandling:
    def test_trashed_nodes_are_excluded_by_default(self, vault: Vault) -> None:
        node_id = add(vault, "deleted-thing.txt")
        with vault.connection as conn:
            conn.execute(
                "UPDATE nodes SET deleted_at = '2026-02-01' WHERE id = ?", (node_id.bytes,)
            )

        assert names(vault, "deleted-thing") == []

    def test_trashed_nodes_can_be_searched_explicitly(self, vault: Vault) -> None:
        node_id = add(vault, "deleted-thing.txt")
        with vault.connection as conn:
            conn.execute(
                "UPDATE nodes SET deleted_at = '2026-02-01' WHERE id = ?", (node_id.bytes,)
            )

        assert names(vault, "deleted-thing", include_trashed=True) == ["deleted-thing.txt"]


class TestResultShape:
    def test_hit_carries_identity_and_kind(self, vault: Vault) -> None:
        node_id = add(vault, "manual.pdf", kind="file")
        hit = search(vault.connection, "manual")[0]
        assert hit.node_id == node_id
        assert hit.kind == "file"
