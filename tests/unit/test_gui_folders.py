"""Folder transfers and drag-and-drop from the file manager."""

import random
from collections.abc import Iterator
from pathlib import Path

import pytest
from PySide6.QtCore import QMimeData, QPoint, Qt, QUrl
from PySide6.QtGui import QDropEvent
from pytestqt.qtbot import QtBot

from disbox.backends.local import LocalBackend
from disbox.core.chunker import ChunkSpec
from disbox.core.crypto import KdfParams
from disbox.core.engine import TransferEngine
from disbox.core.filesystem import FileSystem
from disbox.core.vault import Vault
from disbox.gui.bridge import AsyncBridge
from disbox.gui.views.main_window import MainWindow
from tests.unit.test_gui_window import visible_names

PASSPHRASE = "folder transfer passphrase"  # noqa: S105 - fixture, not a credential
FAST = KdfParams(time_cost=1, memory_cost=8, parallelism=1)
SPEC = ChunkSpec(min_size=256, avg_size=1024, max_size=4096)


@pytest.fixture
def vault(tmp_path: Path) -> Iterator[Vault]:
    with Vault.create_encrypted(tmp_path / "demo.dbx", PASSPHRASE, FAST) as vault:
        yield vault


@pytest.fixture
def bridge() -> Iterator[AsyncBridge]:
    bridge = AsyncBridge()
    bridge.start()
    yield bridge
    bridge.stop()


@pytest.fixture
def window(qtbot: QtBot, vault: Vault, bridge: AsyncBridge, tmp_path: Path) -> MainWindow:
    engine = TransferEngine(
        vault, LocalBackend(tmp_path / "blobs"), vault.unlock(PASSPHRASE), spec=SPEC
    )
    window = MainWindow(vault, bridge=bridge, engine=engine)
    qtbot.addWidget(window)
    return window


def make_tree(root: Path) -> Path:
    """A folder with a file and a subfolder holding another file."""
    root.mkdir()
    (root / "top.txt").write_bytes(random.Random(2).randbytes(3000))  # noqa: S311
    nested = root / "nested"
    nested.mkdir()
    (nested / "deep.txt").write_bytes(b"deep")
    return root


def test_uploading_a_folder_recreates_its_structure(
    qtbot: QtBot, window: MainWindow, vault: Vault, tmp_path: Path
) -> None:
    source = make_tree(tmp_path / "Album")

    with qtbot.waitSignal(window.transfers_idle, timeout=20000):
        window.upload_files([source])

    assert visible_names(window) == ["Album"]
    filesystem = FileSystem(vault)
    album = filesystem.children(None)[0]
    assert sorted(n.name for n in filesystem.children(album.id)) == ["nested", "top.txt"]


def test_uploading_a_folder_carries_nested_files(
    qtbot: QtBot, window: MainWindow, vault: Vault, tmp_path: Path
) -> None:
    source = make_tree(tmp_path / "Album")

    with qtbot.waitSignal(window.transfers_idle, timeout=20000):
        window.upload_files([source])

    filesystem = FileSystem(vault)
    album = filesystem.children(None)[0]
    nested = next(n for n in filesystem.children(album.id) if n.kind == "dir")
    assert [n.name for n in filesystem.children(nested.id)] == ["deep.txt"]


def test_a_folder_round_trips_back_to_disk(
    qtbot: QtBot, window: MainWindow, tmp_path: Path
) -> None:
    payload = random.Random(3).randbytes(2500)  # noqa: S311
    source = tmp_path / "Trip"
    source.mkdir()
    (source / "inner.bin").write_bytes(payload)
    with qtbot.waitSignal(window.transfers_idle, timeout=20000):
        window.upload_files([source])

    window._table.selectRow(0)
    out = tmp_path / "out"
    out.mkdir()
    with qtbot.waitSignal(window.transfers_idle, timeout=20000):
        window.download_selected(out)

    assert (out / "Trip" / "inner.bin").read_bytes() == payload


def test_mixed_files_and_folders_upload_together(
    qtbot: QtBot, window: MainWindow, tmp_path: Path
) -> None:
    folder = make_tree(tmp_path / "Folder")
    loose = tmp_path / "loose.txt"
    loose.write_bytes(b"loose")

    with qtbot.waitSignal(window.transfers_idle, timeout=20000):
        window.upload_files([folder, loose])

    assert sorted(visible_names(window)) == ["Folder", "loose.txt"]


def test_dropping_files_uploads_them(qtbot: QtBot, window: MainWindow, tmp_path: Path) -> None:
    dropped = tmp_path / "dropped.txt"
    dropped.write_bytes(b"from explorer")
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(dropped))])
    event = QDropEvent(
        QPoint(10, 10),
        Qt.DropAction.CopyAction,
        mime,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )

    with qtbot.waitSignal(window.transfers_idle, timeout=20000):
        window.dropEvent(event)

    assert visible_names(window) == ["dropped.txt"]


def test_a_drop_carrying_no_files_is_ignored(window: MainWindow) -> None:
    mime = QMimeData()
    mime.setText("just some text")
    event = QDropEvent(
        QPoint(10, 10),
        Qt.DropAction.CopyAction,
        mime,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )

    window.dropEvent(event)

    assert visible_names(window) == []


def test_the_window_accepts_file_drops(window: MainWindow) -> None:
    assert window.acceptDrops()


def test_folder_failures_are_reported(qtbot: QtBot, window: MainWindow, tmp_path: Path) -> None:
    """An empty folder still lands, so the user sees what they asked for."""
    empty = tmp_path / "Empty"
    empty.mkdir()

    with qtbot.waitSignal(window.transfers_idle, timeout=20000):
        window.upload_files([empty])

    assert visible_names(window) == ["Empty"]
