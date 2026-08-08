"""The properties dialog: everything known about one node."""

import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from pytestqt.qtbot import QtBot

from disbox.core.vault import Vault
from disbox.gui.theme.tokens import DARK
from disbox.gui.views.properties_dialog import PropertiesDialog
from tests.unit.test_gui_window import add
from tests.unit.test_vault import KEYS


@pytest.fixture
def vault(tmp_path: Path) -> Iterator[Vault]:
    with Vault.create(tmp_path / "demo.dbx", KEYS) as vault:
        yield vault


def open_for(qtbot: QtBot, vault: Vault, node_id: object) -> PropertiesDialog:
    dialog = PropertiesDialog(vault, node_id, DARK)  # type: ignore[arg-type]
    qtbot.addWidget(dialog)
    return dialog


def test_shows_the_name(qtbot: QtBot, vault: Vault) -> None:
    node = add(vault, "report.pdf")

    dialog = open_for(qtbot, vault, node)

    assert dialog.field("Name") == "report.pdf"


def test_shows_the_full_path(qtbot: QtBot, vault: Vault) -> None:
    parent = add(vault, "Docs", kind="dir")
    node = add(vault, "deep.txt", parent=parent)

    dialog = open_for(qtbot, vault, node)

    assert "Docs" in dialog.field("Location")
    assert "deep.txt" in dialog.field("Location")


def test_shows_a_readable_size_and_the_exact_bytes(qtbot: QtBot, vault: Vault) -> None:
    node = add(vault, "sized.bin")  # the helper inserts 1024 bytes

    dialog = open_for(qtbot, vault, node)

    assert "1.0 KB" in dialog.field("Size")
    assert "1,024" in dialog.field("Size")


def test_a_folder_reports_its_child_count_not_a_size(qtbot: QtBot, vault: Vault) -> None:
    folder = add(vault, "Album", kind="dir")
    add(vault, "a.txt", parent=folder)
    add(vault, "b.txt", parent=folder)

    dialog = open_for(qtbot, vault, folder)

    assert dialog.field("Type") == "Folder"
    assert "2" in dialog.field("Contents")


def test_a_file_with_no_upload_says_so(qtbot: QtBot, vault: Vault) -> None:
    """Nothing is stored yet, and pretending otherwise would mislead."""
    node = add(vault, "pending.txt")

    dialog = open_for(qtbot, vault, node)

    assert "Not yet uploaded" in dialog.field("Stored")


def test_shows_the_node_id_for_support(qtbot: QtBot, vault: Vault) -> None:
    node = add(vault, "traceable.txt")

    dialog = open_for(qtbot, vault, node)

    assert dialog.field("Identifier") == str(node)


def test_timestamps_are_shown(qtbot: QtBot, vault: Vault) -> None:
    node = add(vault, "dated.txt")

    dialog = open_for(qtbot, vault, node)

    assert dialog.field("Created") != ""
    assert dialog.field("Modified") != ""


def test_a_missing_node_reports_rather_than_crashing(qtbot: QtBot, vault: Vault) -> None:
    dialog = open_for(qtbot, vault, uuid.uuid7())

    assert "not found" in dialog.field("Name").lower() or dialog.field("Name") == ""
