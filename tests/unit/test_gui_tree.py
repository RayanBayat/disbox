"""The folder tree: shows directories only, and expands them lazily."""

import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from pytestqt.qtbot import QtBot

from disbox.core.vault import Vault
from disbox.gui.theme.tokens import DARK
from disbox.gui.views.folder_tree import FolderTree
from tests.unit.test_gui_window import add
from tests.unit.test_vault import KEYS


@pytest.fixture
def vault(tmp_path: Path) -> Iterator[Vault]:
    with Vault.create(tmp_path / "demo.dbx", KEYS) as vault:
        yield vault


@pytest.fixture
def tree(qtbot: QtBot, vault: Vault) -> FolderTree:
    widget = FolderTree(vault, DARK)
    qtbot.addWidget(widget)
    return widget


def labels(tree: FolderTree) -> list[str]:
    root = tree.invisibleRootItem()
    return [root.child(i).text(0) for i in range(root.childCount())]


def test_shows_top_level_folders(tree: FolderTree, vault: Vault) -> None:
    add(vault, "Photos", kind="dir")
    add(vault, "Docs", kind="dir")

    tree.reload()

    assert sorted(labels(tree)) == ["Docs", "Photos"]


def test_files_are_not_shown(tree: FolderTree, vault: Vault) -> None:
    """A folder tree that lists files is just a second file list."""
    add(vault, "Photos", kind="dir")
    add(vault, "notes.txt")

    tree.reload()

    assert labels(tree) == ["Photos"]


def test_children_are_not_read_until_expanded(tree: FolderTree, vault: Vault) -> None:
    parent = add(vault, "Parent", kind="dir")
    add(vault, "Child", kind="dir", parent=parent)
    tree.reload()

    item = tree.invisibleRootItem().child(0)

    # A placeholder marks it expandable without having read the subtree.
    assert item.childCount() == 1
    assert item.child(0).text(0) == ""


def test_expanding_reveals_the_real_children(tree: FolderTree, vault: Vault) -> None:
    parent = add(vault, "Parent", kind="dir")
    add(vault, "Child", kind="dir", parent=parent)
    tree.reload()
    item = tree.invisibleRootItem().child(0)

    tree.expandItem(item)

    assert [item.child(i).text(0) for i in range(item.childCount())] == ["Child"]


def test_a_childless_folder_is_not_expandable(tree: FolderTree, vault: Vault) -> None:
    add(vault, "Empty", kind="dir")

    tree.reload()

    assert tree.invisibleRootItem().child(0).childCount() == 0


def test_selecting_a_folder_announces_it(qtbot: QtBot, tree: FolderTree, vault: Vault) -> None:
    folder = add(vault, "Target", kind="dir")
    tree.reload()

    with qtbot.waitSignal(tree.directory_selected, timeout=1000) as caught:
        tree.setCurrentItem(tree.invisibleRootItem().child(0))

    assert caught.args == [folder]


def test_deleted_folders_are_left_out(tree: FolderTree, vault: Vault) -> None:
    gone = add(vault, "Gone", kind="dir")
    with vault.connection as conn:
        conn.execute(
            "UPDATE nodes SET deleted_at = '2026-01-02T00:00:00Z' WHERE id = ?",
            (gone.bytes,),
        )

    tree.reload()

    assert labels(tree) == []


def test_reload_replaces_rather_than_appends(tree: FolderTree, vault: Vault) -> None:
    add(vault, "One", kind="dir")

    tree.reload()
    tree.reload()

    assert labels(tree) == ["One"]


def test_nested_expansion_works_more_than_one_level_down(tree: FolderTree, vault: Vault) -> None:
    top = add(vault, "Top", kind="dir")
    middle = add(vault, "Middle", kind="dir", parent=top)
    add(vault, "Bottom", kind="dir", parent=middle)
    tree.reload()

    top_item = tree.invisibleRootItem().child(0)
    tree.expandItem(top_item)
    middle_item = top_item.child(0)
    tree.expandItem(middle_item)

    assert middle_item.child(0).text(0) == "Bottom"


def test_folder_id_is_carried_on_the_item(tree: FolderTree, vault: Vault) -> None:
    folder = add(vault, "Named", kind="dir")

    tree.reload()

    assert tree.node_id_of(tree.invisibleRootItem().child(0)) == folder
    assert isinstance(folder, uuid.UUID)
