"""The file table must stay fast by reading pages from SQLite, never the whole tree."""

import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from PySide6.QtCore import QModelIndex, Qt

from disbox.core.vault import Vault
from disbox.gui.models.file_table import Column, FileTableModel
from tests.unit.test_vault import KEYS


@pytest.fixture
def vault(tmp_path: Path) -> Iterator[Vault]:
    with Vault.create(tmp_path / "test.dbx", KEYS) as vault:
        yield vault


def add(vault: Vault, name: str, *, kind: str = "file", size: int = 0) -> uuid.UUID:
    node_id = uuid.uuid7()
    with vault.connection as conn:
        conn.execute(
            "INSERT INTO nodes (id, parent_id, name, kind, size, created_at, modified_at) "
            "VALUES (?, NULL, ?, ?, ?, '2026-01-01T00:00:00Z', '2026-02-03T04:05:06Z')",
            (node_id.bytes, name, kind, size),
        )
    return node_id


class TestShape:
    def test_row_count_matches_the_directory(self, vault: Vault) -> None:
        for index in range(7):
            add(vault, f"file-{index}.txt")
        model = FileTableModel(vault)
        model.set_directory(None)
        assert model.rowCount() == 7

    def test_empty_directory_has_no_rows(self, vault: Vault) -> None:
        model = FileTableModel(vault)
        model.set_directory(None)
        assert model.rowCount() == 0

    def test_every_column_has_a_header(self, vault: Vault) -> None:
        model = FileTableModel(vault)
        for column in Column:
            header = model.headerData(
                column, Qt.Orientation.Horizontal, Qt.ItemDataRole.DisplayRole
            )
            assert header, f"column {column!r} has no header text"


class TestDisplay:
    def test_name_is_shown(self, vault: Vault) -> None:
        add(vault, "report.pdf")
        model = FileTableModel(vault)
        model.set_directory(None)
        assert model.data(model.index(0, Column.NAME), Qt.ItemDataRole.DisplayRole) == "report.pdf"

    def test_size_is_human_readable(self, vault: Vault) -> None:
        add(vault, "big.bin", size=2_500_000)
        model = FileTableModel(vault)
        model.set_directory(None)
        shown = model.data(model.index(0, Column.SIZE), Qt.ItemDataRole.DisplayRole)
        assert "MB" in shown or "MiB" in shown, shown

    def test_directories_show_no_size(self, vault: Vault) -> None:
        add(vault, "folder", kind="dir")
        model = FileTableModel(vault)
        model.set_directory(None)
        assert model.data(model.index(0, Column.SIZE), Qt.ItemDataRole.DisplayRole) == ""

    def test_directories_sort_before_files(self, vault: Vault) -> None:
        add(vault, "zzz-folder", kind="dir")
        add(vault, "aaa-file.txt")
        model = FileTableModel(vault)
        model.set_directory(None)
        first = model.data(model.index(0, Column.NAME), Qt.ItemDataRole.DisplayRole)
        assert first == "zzz-folder", "folders belong at the top regardless of name"

    def test_out_of_range_index_returns_nothing(self, vault: Vault) -> None:
        model = FileTableModel(vault)
        model.set_directory(None)
        assert model.data(model.index(99, Column.NAME), Qt.ItemDataRole.DisplayRole) is None

    def test_invalid_index_returns_nothing(self, vault: Vault) -> None:
        model = FileTableModel(vault)
        assert model.data(QModelIndex(), Qt.ItemDataRole.DisplayRole) is None


class TestPaging:
    def test_only_requested_pages_are_read(self, vault: Vault) -> None:
        """Scrolling must not drag the whole directory into memory."""
        for index in range(500):
            add(vault, f"file-{index:04d}.txt")
        model = FileTableModel(vault, page_size=50)
        model.set_directory(None)

        model.data(model.index(0, Column.NAME), Qt.ItemDataRole.DisplayRole)
        assert model.rows_cached() <= 100, "reading one row pulled far more than its page"

    def test_distant_rows_are_still_correct(self, vault: Vault) -> None:
        for index in range(500):
            add(vault, f"file-{index:04d}.txt")
        model = FileTableModel(vault, page_size=50)
        model.set_directory(None)

        last = model.data(model.index(499, Column.NAME), Qt.ItemDataRole.DisplayRole)
        assert last == "file-0499.txt"

    def test_cache_is_dropped_when_the_directory_changes(self, vault: Vault) -> None:
        folder = add(vault, "sub", kind="dir")
        for index in range(20):
            add(vault, f"file-{index}.txt")
        model = FileTableModel(vault, page_size=10)
        model.set_directory(None)
        model.data(model.index(0, Column.NAME), Qt.ItemDataRole.DisplayRole)

        model.set_directory(folder)
        assert model.rowCount() == 0
        assert model.rows_cached() == 0


class TestRefresh:
    def test_refresh_picks_up_new_rows(self, vault: Vault) -> None:
        add(vault, "first.txt")
        model = FileTableModel(vault)
        model.set_directory(None)
        assert model.rowCount() == 1

        add(vault, "second.txt")
        model.refresh()
        assert model.rowCount() == 2

    def test_trashed_nodes_are_hidden(self, vault: Vault) -> None:
        node_id = add(vault, "gone.txt")
        with vault.connection as conn:
            conn.execute(
                "UPDATE nodes SET deleted_at = '2026-03-01' WHERE id = ?", (node_id.bytes,)
            )
        model = FileTableModel(vault)
        model.set_directory(None)
        assert model.rowCount() == 0

    def test_node_id_is_retrievable_for_a_row(self, vault: Vault) -> None:
        node_id = add(vault, "target.txt")
        model = FileTableModel(vault)
        model.set_directory(None)
        assert model.node_id_at(0) == node_id
