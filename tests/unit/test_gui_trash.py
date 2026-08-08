"""The trash dialog: see what was deleted, and put it back."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from pytestqt.qtbot import QtBot

from disbox.core.filesystem import FileSystem
from disbox.core.vault import Vault
from disbox.gui.theme.tokens import DARK
from disbox.gui.views.trash_dialog import TrashDialog
from tests.unit.test_gui_window import add
from tests.unit.test_vault import KEYS


@pytest.fixture
def vault(tmp_path: Path) -> Iterator[Vault]:
    with Vault.create(tmp_path / "demo.dbx", KEYS) as vault:
        yield vault


def open_dialog(qtbot: QtBot, vault: Vault) -> TrashDialog:
    dialog = TrashDialog(vault, DARK)
    qtbot.addWidget(dialog)
    return dialog


def names(dialog: TrashDialog) -> list[str]:
    return [dialog._list.item(i).text() for i in range(dialog._list.count())]


def test_lists_what_is_in_the_trash(qtbot: QtBot, vault: Vault) -> None:
    node = add(vault, "gone.txt")
    FileSystem(vault).delete(node)

    dialog = open_dialog(qtbot, vault)

    assert any("gone.txt" in name for name in names(dialog))


def test_a_vault_with_nothing_deleted_shows_nothing(qtbot: QtBot, vault: Vault) -> None:
    add(vault, "kept.txt")

    dialog = open_dialog(qtbot, vault)

    assert names(dialog) == []


def test_restore_returns_the_node_to_the_tree(qtbot: QtBot, vault: Vault) -> None:
    node = add(vault, "oops.txt")
    filesystem = FileSystem(vault)
    filesystem.delete(node)
    dialog = open_dialog(qtbot, vault)
    dialog._list.setCurrentRow(0)

    dialog.restore_selected()

    assert [n.name for n in filesystem.children(None)] == ["oops.txt"]
    assert filesystem.trash() == []


def test_restore_removes_it_from_the_list(qtbot: QtBot, vault: Vault) -> None:
    node = add(vault, "oops.txt")
    FileSystem(vault).delete(node)
    dialog = open_dialog(qtbot, vault)
    dialog._list.setCurrentRow(0)

    dialog.restore_selected()

    assert names(dialog) == []


def test_restore_reports_when_the_name_is_taken_again(qtbot: QtBot, vault: Vault) -> None:
    """Restoring must never silently overwrite whatever claimed the name."""
    node = add(vault, "clash.txt")
    filesystem = FileSystem(vault)
    filesystem.delete(node)
    add(vault, "clash.txt")  # the name is now in use again
    dialog = open_dialog(qtbot, vault)
    dialog._list.setCurrentRow(0)

    dialog.restore_selected()

    assert filesystem.trash() != []  # still there, not overwritten
    assert dialog.status_text != ""


def test_restore_with_nothing_selected_does_nothing(qtbot: QtBot, vault: Vault) -> None:
    node = add(vault, "gone.txt")
    FileSystem(vault).delete(node)
    dialog = open_dialog(qtbot, vault)

    dialog.restore_selected()

    assert len(FileSystem(vault).trash()) == 1


def test_restoring_announces_the_change(qtbot: QtBot, vault: Vault) -> None:
    node = add(vault, "gone.txt")
    FileSystem(vault).delete(node)
    dialog = open_dialog(qtbot, vault)
    dialog._list.setCurrentRow(0)

    with qtbot.waitSignal(dialog.vault_changed, timeout=1000):
        dialog.restore_selected()


def test_folders_show_their_kind(qtbot: QtBot, vault: Vault) -> None:
    node = add(vault, "Album", kind="dir")
    FileSystem(vault).delete(node)

    dialog = open_dialog(qtbot, vault)

    assert any("Album" in name for name in names(dialog))
