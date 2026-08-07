"""The main window: browse, navigate, and search a vault."""

import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from pytestqt.qtbot import QtBot

from disbox.core.vault import Vault
from disbox.gui.models.file_table import Column
from disbox.gui.views.main_window import MainWindow
from tests.unit.test_vault import KEYS


@pytest.fixture
def vault(tmp_path: Path) -> Iterator[Vault]:
    with Vault.create(tmp_path / "demo.dbx", KEYS) as vault:
        yield vault


def add(
    vault: Vault, name: str, *, kind: str = "file", parent: uuid.UUID | None = None
) -> uuid.UUID:
    node_id = uuid.uuid7()
    with vault.connection as conn:
        conn.execute(
            "INSERT INTO nodes (id, parent_id, name, kind, size, created_at, modified_at) "
            "VALUES (?, ?, ?, ?, 1024, '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')",
            (node_id.bytes, parent.bytes if parent else None, name, kind),
        )
    return node_id


def visible_names(window: MainWindow) -> list[str]:
    """Read the names the table is actually displaying.

    Asserting on the window's own result list instead let a bug through where
    search updated the status bar but never the table.
    """
    model = window.table_model
    return [
        model.data(model.index(row, Column.NAME), Qt.ItemDataRole.DisplayRole)
        for row in range(model.rowCount())
    ]


@pytest.fixture
def window(qtbot: QtBot, vault: Vault) -> MainWindow:
    win = MainWindow(vault)
    qtbot.addWidget(win)
    return win


class TestConstruction:
    def test_window_opens_with_the_vault_name_in_the_title(self, window: MainWindow) -> None:
        assert "demo" in window.windowTitle()

    def test_table_shows_the_root_directory(self, vault: Vault, qtbot: QtBot) -> None:
        add(vault, "alpha.txt")
        add(vault, "beta.txt")
        win = MainWindow(vault)
        qtbot.addWidget(win)
        assert win.table_model.rowCount() == 2

    def test_status_bar_reports_the_item_count(self, vault: Vault, qtbot: QtBot) -> None:
        add(vault, "only.txt")
        win = MainWindow(vault)
        qtbot.addWidget(win)
        assert "1" in win.statusBar().currentMessage()


class TestNavigation:
    def test_entering_a_folder_shows_its_contents(self, vault: Vault, qtbot: QtBot) -> None:
        folder = add(vault, "Documents", kind="dir")
        add(vault, "inside.txt", parent=folder)
        add(vault, "outside.txt")

        win = MainWindow(vault)
        qtbot.addWidget(win)
        win.navigate_to(folder)

        assert win.table_model.rowCount() == 1
        assert win.current_directory == folder

    def test_going_up_returns_to_the_parent(self, vault: Vault, qtbot: QtBot) -> None:
        folder = add(vault, "Documents", kind="dir")
        add(vault, "inside.txt", parent=folder)

        win = MainWindow(vault)
        qtbot.addWidget(win)
        win.navigate_to(folder)
        win.navigate_up()

        assert win.current_directory is None

    def test_going_up_from_the_root_is_harmless(self, window: MainWindow) -> None:
        window.navigate_up()
        assert window.current_directory is None

    def test_back_returns_to_the_previous_directory(self, vault: Vault, qtbot: QtBot) -> None:
        first = add(vault, "First", kind="dir")
        second = add(vault, "Second", kind="dir", parent=first)

        win = MainWindow(vault)
        qtbot.addWidget(win)
        win.navigate_to(first)
        win.navigate_to(second)
        win.navigate_back()

        assert win.current_directory == first

    def test_back_at_the_start_of_history_is_harmless(self, window: MainWindow) -> None:
        window.navigate_back()
        assert window.current_directory is None

    def test_breadcrumb_reflects_the_current_location(self, vault: Vault, qtbot: QtBot) -> None:
        folder = add(vault, "Photos", kind="dir")
        nested = add(vault, "2026", kind="dir", parent=folder)

        win = MainWindow(vault)
        qtbot.addWidget(win)
        win.navigate_to(nested)

        assert "Photos" in win.breadcrumb_text()
        assert "2026" in win.breadcrumb_text()


class TestSearch:
    def test_searching_shows_matches_from_anywhere(self, vault: Vault, qtbot: QtBot) -> None:
        folder = add(vault, "Deep", kind="dir")
        add(vault, "needle.txt", parent=folder)
        add(vault, "haystack.txt")

        win = MainWindow(vault)
        qtbot.addWidget(win)
        win.apply_search("needle")

        assert visible_names(win) == ["needle.txt"]
        assert win.table_model.rowCount() == 1, "the table must show only the matches"

    def test_clearing_the_search_restores_the_directory(self, vault: Vault, qtbot: QtBot) -> None:
        add(vault, "one.txt")
        add(vault, "two.txt")

        win = MainWindow(vault)
        qtbot.addWidget(win)
        win.apply_search("one")
        assert visible_names(win) == ["one.txt"]

        win.apply_search("")
        assert sorted(visible_names(win)) == ["one.txt", "two.txt"]

    def test_a_search_with_no_matches_shows_nothing(self, window: MainWindow) -> None:
        window.apply_search("nothing-matches-this")
        assert visible_names(window) == []
        assert window.table_model.rowCount() == 0
