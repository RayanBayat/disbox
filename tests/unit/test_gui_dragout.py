"""Dragging files out to Explorer without downloading them first."""

import random
from collections.abc import Iterator
from pathlib import Path

import pytest
from PySide6.QtCore import QMimeData, Qt
from pytestqt.qtbot import QtBot

from disbox.backends.local import LocalBackend
from disbox.core.chunker import ChunkSpec
from disbox.core.crypto import KdfParams
from disbox.core.engine import TransferEngine
from disbox.core.vault import Vault
from disbox.gui.bridge import AsyncBridge
from disbox.gui.views.main_window import MainWindow

PASSPHRASE = "drag out passphrase"  # noqa: S105 - fixture, not a credential
FAST = KdfParams(time_cost=1, memory_cost=8, parallelism=1)
SPEC = ChunkSpec(min_size=256, avg_size=1024, max_size=4096)


@pytest.fixture
def vault(tmp_path: Path) -> Iterator[Vault]:
    with Vault.create_encrypted(tmp_path / "drag.dbx", PASSPHRASE, FAST) as vault:
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


@pytest.fixture
def stored(qtbot: QtBot, window: MainWindow, tmp_path: Path) -> bytes:
    """A file already in the vault, and its bytes."""
    data = random.Random(4).randbytes(9000)  # noqa: S311 - test data, not a key
    source = tmp_path / "dragged.bin"
    source.write_bytes(data)
    with qtbot.waitSignal(window.transfers_idle, timeout=20000):
        window.upload_files([source])
    return data


@pytest.mark.usefixtures("stored")
def test_dragging_produces_mime_data(window: MainWindow) -> None:
    window._table.selectRow(0)

    mime = window.build_drag_mime()

    assert mime is not None
    assert mime.hasUrls() or mime.hasFormat("text/uri-list")


@pytest.mark.usefixtures("stored")
def test_nothing_is_written_until_the_data_is_asked_for(window: MainWindow) -> None:
    """Starting a drag must not download; the user may never drop it."""
    window._table.selectRow(0)

    mime = window.build_drag_mime()

    assert mime is not None
    assert not mime.materialised, "the drag downloaded before it was dropped"


def test_asking_for_the_data_writes_the_file(window: MainWindow, stored: bytes) -> None:
    window._table.selectRow(0)
    mime = window.build_drag_mime()
    assert mime is not None

    urls = mime.urls()

    assert len(urls) == 1
    written = Path(urls[0].toLocalFile())
    assert written.read_bytes() == stored


@pytest.mark.usefixtures("stored")
def test_the_dropped_file_keeps_its_name(window: MainWindow) -> None:
    window._table.selectRow(0)
    mime = window.build_drag_mime()
    assert mime is not None

    written = Path(mime.urls()[0].toLocalFile())

    assert written.name == "dragged.bin"


def test_dragging_nothing_produces_no_mime_data(window: MainWindow) -> None:
    window._table.clearSelection()

    assert window.build_drag_mime() is None


@pytest.mark.usefixtures("stored")
def test_the_data_is_only_produced_once(window: MainWindow) -> None:
    """Explorer asks more than once; downloading each time would be absurd."""
    window._table.selectRow(0)
    mime = window.build_drag_mime()
    assert mime is not None

    first = mime.urls()
    second = mime.urls()

    assert [u.toLocalFile() for u in first] == [u.toLocalFile() for u in second]


def test_several_selected_files_all_come_out(
    qtbot: QtBot, window: MainWindow, tmp_path: Path
) -> None:
    for index in range(3):
        source = tmp_path / f"multi{index}.bin"
        source.write_bytes(b"x" * (100 + index))
        with qtbot.waitSignal(window.transfers_idle, timeout=20000):
            window.upload_files([source])

    window._table.selectAll()
    mime = window.build_drag_mime()
    assert mime is not None

    assert len(mime.urls()) == 3


@pytest.mark.usefixtures("stored")
def test_it_is_mime_data_qt_can_carry(window: MainWindow) -> None:
    """Qt's drag machinery requires a QMimeData, not a look-alike."""
    window._table.selectRow(0)

    assert isinstance(window.build_drag_mime(), QMimeData)


@pytest.mark.usefixtures("stored")
def test_a_drag_starts_with_a_copy_action(window: MainWindow) -> None:
    """Moving would delete from the vault, which a drag to Explorer must not."""
    window._table.selectRow(0)

    assert window.drag_actions() == Qt.DropAction.CopyAction
