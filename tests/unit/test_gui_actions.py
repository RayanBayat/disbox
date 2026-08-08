"""Creating, renaming, and deleting nodes from the main window."""

import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from pytestqt.qtbot import QtBot

from disbox.core.filesystem import FileSystem
from disbox.core.vault import Vault
from disbox.gui.views.main_window import MainWindow
from tests.unit.test_gui_window import add, visible_names
from tests.unit.test_vault import KEYS


@pytest.fixture
def vault(tmp_path: Path) -> Iterator[Vault]:
    with Vault.create(tmp_path / "demo.dbx", KEYS) as vault:
        yield vault


@pytest.fixture
def window(qtbot: QtBot, vault: Vault) -> MainWindow:
    window = MainWindow(vault)
    qtbot.addWidget(window)
    return window


def test_create_folder_shows_it_in_the_current_directory(window: MainWindow) -> None:
    window.create_folder("Invoices")

    assert "Invoices" in visible_names(window)


def test_create_folder_nests_under_the_open_directory(window: MainWindow, vault: Vault) -> None:
    parent = add(vault, "Parent", kind="dir")
    window._refresh()
    window.navigate_to(parent)

    window.create_folder("Child")

    assert visible_names(window) == ["Child"]
    assert FileSystem(vault).children(parent)[0].name == "Child"


def test_create_folder_sidesteps_a_name_already_in_use(window: MainWindow, vault: Vault) -> None:
    add(vault, "Reports", kind="dir")
    window._refresh()

    window.create_folder("Reports")

    names = visible_names(window)
    assert "Reports" in names
    # The second folder is kept, under a name that does not collide.
    assert len([n for n in names if n.startswith("Reports")]) == 2


def test_rename_selected_updates_the_listing(window: MainWindow, vault: Vault) -> None:
    add(vault, "draft.txt")
    window._refresh()
    window._table.selectRow(0)

    window.rename_selected("final.txt")

    assert visible_names(window) == ["final.txt"]


def test_rename_selected_refuses_a_name_already_in_use(window: MainWindow, vault: Vault) -> None:
    add(vault, "keep.txt")
    add(vault, "other.txt")
    window._refresh()
    window._table.selectRow(0)

    window.rename_selected("other.txt")

    # Nothing is renamed, and neither file is lost to a partial rename.
    assert sorted(visible_names(window)) == ["keep.txt", "other.txt"]
    assert "already" in window.status_text().lower()


def test_delete_selected_removes_it_from_the_listing(window: MainWindow, vault: Vault) -> None:
    add(vault, "junk.txt")
    add(vault, "keep.txt")
    window._refresh()
    window._table.selectRow(visible_names(window).index("junk.txt"))

    window.delete_selected()

    assert visible_names(window) == ["keep.txt"]


def test_delete_selected_removes_every_selected_row(window: MainWindow, vault: Vault) -> None:
    for name in ("a.txt", "b.txt", "c.txt"):
        add(vault, name)
    window._refresh()
    window._table.selectAll()

    window.delete_selected()

    assert visible_names(window) == []


def test_delete_selected_is_recoverable_from_the_trash(window: MainWindow, vault: Vault) -> None:
    node = add(vault, "oops.txt")
    window._refresh()
    window._table.selectRow(0)

    window.delete_selected()

    assert [n.id for n in FileSystem(vault).trash()] == [node]


def test_rename_with_nothing_selected_does_nothing(window: MainWindow) -> None:
    window.rename_selected("whatever")

    assert visible_names(window) == []


def test_create_folder_selects_what_it_created(window: MainWindow) -> None:
    window.create_folder("Archive")

    selected = {index.row() for index in window._table.selectionModel().selectedRows()}
    assert len(selected) == 1
    assert window.table_model.node_id_at(next(iter(selected))) is not None


def test_deleted_node_leaves_the_details_pane(window: MainWindow, vault: Vault) -> None:
    add(vault, "gone.txt")
    window._refresh()
    window._table.selectRow(0)
    window._on_selection_changed()
    # isVisibleTo, not isVisible: the window is never shown in tests, and every
    # child of an unshown window reports itself invisible regardless of state.
    assert window.details.isVisibleTo(window)

    window.delete_selected()

    assert not window.details.isVisibleTo(window)


def test_new_folder_uses_a_default_name_when_none_is_given(window: MainWindow) -> None:
    window.create_folder()

    assert visible_names(window) == ["New folder"]


def test_node_ids_survive_a_rename(window: MainWindow, vault: Vault) -> None:
    node = add(vault, "before.txt")
    window._refresh()
    window._table.selectRow(0)

    window.rename_selected("after.txt")

    assert window.table_model.node_id_at(0) == node
    assert isinstance(node, uuid.UUID)
