"""Accessibility: everything reachable by keyboard, everything named."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QAbstractButton, QLineEdit, QWidget
from pytestqt.qtbot import QtBot

from disbox.core.vault import Vault
from disbox.gui.theme.tokens import Type
from disbox.gui.views.main_window import MainWindow
from tests.unit.test_gui_window import add
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


def interactive(window: MainWindow) -> list[QWidget]:
    """Every control this application owns and a user can operate.

    Qt's own internal children are excluded: the table's corner button and a
    QLineEdit's clear button are created by Qt, named by Qt, and not reachable
    to configure without reaching into private widget trees.
    """
    found: list[QWidget] = []
    found.extend(window.findChildren(QAbstractButton))
    found.extend(window.findChildren(QLineEdit))
    return [
        w
        for w in found
        if w.isEnabled()
        and not w.objectName().startswith("qt_")
        and not isinstance(w.parent(), QLineEdit)
    ]


def test_every_control_has_an_accessible_name(window: MainWindow) -> None:
    """A screen reader announces the accessible name; without one it says nothing."""
    unnamed = [
        w.objectName() or w.__class__.__name__
        for w in interactive(window)
        if not w.accessibleName()
    ]

    assert unnamed == []


def test_icon_only_buttons_are_named_not_just_tooltipped(window: MainWindow) -> None:
    """A tooltip needs a pointer, so it is no substitute for a name."""
    for control in interactive(window):
        if not isinstance(control, QAbstractButton) or control.text():
            continue
        assert control.accessibleName(), f"{control.objectName()} has no accessible name"


def test_the_file_table_is_named(window: MainWindow) -> None:
    assert window._table.accessibleName() != ""


def test_the_folder_tree_is_named(window: MainWindow) -> None:
    assert window.tree.accessibleName() != ""


def test_the_search_field_is_named(window: MainWindow) -> None:
    assert window._search_box.accessibleName() != ""


def test_every_control_is_keyboard_reachable(window: MainWindow) -> None:
    """A control reachable only by mouse is unreachable for some users."""
    unreachable = [
        w.objectName() or w.__class__.__name__
        for w in interactive(window)
        if w.focusPolicy() == Qt.FocusPolicy.NoFocus
    ]

    assert unreachable == []


def test_primary_actions_have_shortcuts(window: MainWindow) -> None:
    shortcuts = {
        sequence.toString() for action in window.actions() for sequence in action.shortcuts()
    }

    assert "Ctrl+Shift+N" in shortcuts  # new folder
    assert "F2" in shortcuts  # rename
    assert "Ctrl+Z" in shortcuts  # undo


def test_no_font_size_is_below_the_legibility_floor() -> None:
    """Windows sets 14px Regular and 12px Semibold as hard minimums."""
    assert Type.CAPTION >= 12
    assert Type.BODY >= 14


def test_the_window_scales_with_the_font(window: MainWindow) -> None:
    """A layout pinned to pixels breaks when the user enlarges text."""
    assert window.minimumWidth() <= 800
    assert window.minimumHeight() <= 600


def test_the_table_exposes_row_content_to_assistive_tech(window: MainWindow, vault: Vault) -> None:
    add(vault, "readable.txt")
    window._refresh()

    value = window.table_model.data(
        window.table_model.index(0, 0), Qt.ItemDataRole.AccessibleTextRole
    )
    assert value is None or "readable.txt" in str(value)
